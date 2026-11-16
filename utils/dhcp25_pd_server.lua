#!/usr/bin/lua
-- Minimal DHCPv6 IA_PD server for DHCP_25 lab automation.
-- Used because this platform's dnsmasq rejects PD ranges and stock odhcpd
-- does not answer IA_PD Solicits on br-lan.
local nixio = require "nixio"

local function rshift(a, n)
	return math.floor((a % 4294967296) / (2 ^ n))
end
local function lshift(a, n)
	return (a * (2 ^ n)) % 4294967296
end
local function u16(n)
	return string.char(rshift(n, 8) % 256, n % 256)
end
local function u32(n)
	return string.char(
		rshift(n, 24) % 256,
		rshift(n, 16) % 256,
		rshift(n, 8) % 256,
		n % 256
	)
end
local function ru16(s, i)
	return lshift(s:byte(i), 8) + s:byte(i + 1), i + 2
end

local PREFIX = os.getenv("PD_PREFIX") or "2401:4900:d0:40e0:1::"
local PLEN = tonumber(os.getenv("PD_PLEN") or "64")
local LEASE = tonumber(os.getenv("PD_LEASE") or "300")
local LOG = os.getenv("PD_LOG") or "/tmp/dhcp25_pd_srv.log"
local ASSIGN = os.getenv("PD_ASSIGN") or "/tmp/dhcp25_pd_assigned"
local PIDFILE = os.getenv("PD_PID") or "/tmp/dhcp25_pd_srv.pid"

local function log(msg)
	local f = io.open(LOG, "a")
	if f then
		f:write(os.date("%H:%M:%S ") .. tostring(msg) .. "\n")
		f:close()
	end
end

local function inet_pton6(str)
	local head, tail = str:match("^(.-)::(.*)$")
	local parts = {}
	if head then
		local left, right = {}, {}
		if head ~= "" then
			for p in head:gmatch("[^:]+") do
				left[#left + 1] = tonumber(p, 16) or 0
			end
		end
		if tail ~= "" then
			for p in tail:gmatch("[^:]+") do
				right[#right + 1] = tonumber(p, 16) or 0
			end
		end
		for _, v in ipairs(left) do
			parts[#parts + 1] = v
		end
		for _ = 1, (8 - #left - #right) do
			parts[#parts + 1] = 0
		end
		for _, v in ipairs(right) do
			parts[#parts + 1] = v
		end
	else
		for p in str:gmatch("[^:]+") do
			parts[#parts + 1] = tonumber(p, 16) or 0
		end
	end
	while #parts < 8 do
		parts[#parts + 1] = 0
	end
	local out = ""
	for i = 1, 8 do
		out = out .. u16(parts[i])
	end
	return out
end

local function opt(code, data)
	return u16(code) .. u16(#data) .. data
end

local function find_opts(buf, start, stop)
	local i, opts = start, {}
	while i + 3 <= stop do
		local code, len
		code, i = ru16(buf, i)
		len, i = ru16(buf, i)
		if i + len - 1 > stop then
			break
		end
		opts[#opts + 1] = { code = code, data = buf:sub(i, i + len - 1) }
		i = i + len
	end
	return opts
end

local function find_opt(opts, code)
	for _, o in ipairs(opts) do
		if o.code == code then
			return o
		end
	end
end

local SERVER_DUID = "\000\001\000\001" .. u32(os.time()) .. "DHCP25PD"
local PREFIX_BIN = inet_pton6(PREFIX)

local function build_ia_pd(iaid)
	local t1 = math.floor(LEASE * 0.5)
	local t2 = math.floor(LEASE * 0.8)
	local pref = math.floor(LEASE * 0.8)
	local iapfx = u32(pref) .. u32(LEASE) .. string.char(PLEN) .. PREFIX_BIN
	return opt(25, iaid .. u32(t1) .. u32(t2) .. opt(26, iapfx))
end

local function reply_msg(mtype, xid, client_duid, iaid)
	return string.char(mtype)
		.. xid
		.. opt(1, client_duid)
		.. opt(2, SERVER_DUID)
		.. build_ia_pd(iaid)
		.. opt(7, string.char(255))
end

local s = assert(nixio.socket("inet6", "dgram"))
s:setsockopt("socket", "reuseaddr", 1)
pcall(function()
	s:setsockopt("ipv6", "v6only", 1)
end)
assert(s:bind("::", 547), "bind :547 failed")
-- nixio accepts multicast group as a string for add_membership
local jok, jres = pcall(function()
	return s:setsockopt("ipv6", "add_membership", "ff02::1:2")
end)
pcall(function()
	s:setsockopt("socket", "bindtodevice", "br-lan")
end)

local pf = io.open(PIDFILE, "w")
if pf then
	pf:write(tostring(nixio.getpid()))
	pf:close()
end
log(
	string.format(
		"server up prefix=%s/%d join_ok=%s join_res=%s",
		PREFIX,
		PLEN,
		tostring(jok),
		tostring(jres)
	)
)

local function handle(buf, host, port)
	if #buf < 4 then
		return
	end
	local mtype = buf:byte(1)
	local xid = buf:sub(2, 4)
	local opts = find_opts(buf, 5, #buf)
	local cid = find_opt(opts, 1)
	if not cid then
		return
	end
	local iapd = find_opt(opts, 25)
	local iaid = "\000\000\000\001"
	if iapd and #iapd.data >= 4 then
		iaid = iapd.data:sub(1, 4)
	end
	log(string.format("msg=%d from=%s", mtype, tostring(host)))
	if mtype == 1 then
		s:sendto(reply_msg(2, xid, cid.data, iaid), host, port)
		log("ADVERTISE")
	elseif mtype == 3 or mtype == 5 or mtype == 6 then
		s:sendto(reply_msg(7, xid, cid.data, iaid), host, port)
		log("REPLY")
		local f = io.open(ASSIGN, "w")
		if f then
			f:write(PREFIX .. "/" .. PLEN .. "\n")
			f:close()
		end
	elseif mtype == 8 then
		log("RELEASE ignored")
	end
end

while true do
	pcall(function()
		s:setopt("socket", "rcvtimeo", 1)
	end)
	local data, host, port = s:recvfrom(2048)
	if data then
		local ok, err = pcall(handle, data, host, port)
		if not ok then
			log("err " .. tostring(err))
		end
	else
		nixio.nanosleep(0, 20000000)
	end
end
