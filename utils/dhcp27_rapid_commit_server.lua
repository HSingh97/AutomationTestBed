#!/usr/bin/lua
-- Minimal DHCPv6 IA_NA Rapid Commit server for DHCP_27 lab automation.
-- dnsmasq 2.80 on this platform only documents DHCPv4 rapid-commit and still
-- does Solicit/Advertise/Request/Reply for DHCPv6. This responder answers
-- Solicit with Reply (option Rapid Commit) for a true 2-message exchange.
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

local ADDR = os.getenv("RC_ADDR") or "2401:4900:d0:40d4:0:17b8:0:3c1"
local LEASE = tonumber(os.getenv("RC_LEASE") or "300")
local LOG = os.getenv("RC_LOG") or "/tmp/dhcp27_rc_srv.log"
local ASSIGN = os.getenv("RC_ASSIGN") or "/tmp/dhcp27_rc_assigned"
local PIDFILE = os.getenv("RC_PID") or "/tmp/dhcp27_rc_srv.pid"

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

local SERVER_DUID = "\000\001\000\001" .. u32(os.time()) .. "DHCP27RC"
local ADDR_BIN = inet_pton6(ADDR)

local function build_ia_na(iaid)
	local t1 = math.floor(LEASE * 0.5)
	local t2 = math.floor(LEASE * 0.8)
	local pref = math.floor(LEASE * 0.8)
	-- IAADDR (5): addr(16) + preferred(4) + valid(4)
	local iaaddr = ADDR_BIN .. u32(pref) .. u32(LEASE)
	return opt(3, iaid .. u32(t1) .. u32(t2) .. opt(5, iaaddr))
end

local function reply_msg(mtype, xid, client_duid, iaid, with_rc)
	local body = string.char(mtype)
		.. xid
		.. opt(1, client_duid)
		.. opt(2, SERVER_DUID)
		.. build_ia_na(iaid)
	if with_rc then
		body = body .. opt(14, "") -- Rapid Commit
	end
	body = body .. opt(7, string.char(255)) -- Preference
	return body
end

local s = assert(nixio.socket("inet6", "dgram"))
s:setsockopt("socket", "reuseaddr", 1)
pcall(function()
	s:setsockopt("ipv6", "v6only", 1)
end)
assert(s:bind("::", 547), "bind :547 failed")
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
		"server up addr=%s lease=%d join_ok=%s join_res=%s",
		ADDR,
		LEASE,
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
	local iana = find_opt(opts, 3)
	local iaid = "\000\000\000\001"
	if iana and #iana.data >= 4 then
		iaid = iana.data:sub(1, 4)
	end
	local has_rc = find_opt(opts, 14) ~= nil
	log(string.format("msg=%d from=%s rc=%s", mtype, tostring(host), tostring(has_rc)))
	if mtype == 1 then -- SOLICIT
		-- Prefer Rapid Commit Reply whenever client asked; also accept Solicit
		-- without option 14 only if RC_FORCE=1 (lab fallback).
		local force = os.getenv("RC_FORCE") == "1"
		if has_rc or force then
			s:sendto(reply_msg(7, xid, cid.data, iaid, true), host, port)
			log("REPLY rapid-commit")
			local f = io.open(ASSIGN, "w")
			if f then
				f:write(ADDR .. "\n")
				f:close()
			end
		else
			s:sendto(reply_msg(2, xid, cid.data, iaid, false), host, port)
			log("ADVERTISE")
		end
	elseif mtype == 3 or mtype == 5 or mtype == 6 then
		s:sendto(reply_msg(7, xid, cid.data, iaid, false), host, port)
		log("REPLY")
		local f = io.open(ASSIGN, "w")
		if f then
			f:write(ADDR .. "\n")
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
