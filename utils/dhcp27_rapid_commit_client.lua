#!/usr/bin/lua
-- Minimal DHCPv6 Solicit+Rapid-Commit client for DHCP_27 (CPE side).
-- Stock odhcp6c on this platform does not include OPTION_RAPID_COMMIT.
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

local IFACE = os.getenv("RC_IFACE") or "br-lan"
local OUT = os.getenv("RC_OUT") or "/tmp/dhcp27_rc_client.out"
local LOG = os.getenv("RC_CLOG") or "/tmp/dhcp27_rc_client.log"
local TIMEOUT = tonumber(os.getenv("RC_TIMEOUT") or "8")

local function log(msg)
	local f = io.open(LOG, "a")
	if f then
		f:write(os.date("%H:%M:%S ") .. tostring(msg) .. "\n")
		f:close()
	end
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

local function inet_ntop6(bin)
	if #bin < 16 then
		return ""
	end
	local parts = {}
	for i = 1, 16, 2 do
		parts[#parts + 1] = string.format("%x", lshift(bin:byte(i), 8) + bin:byte(i + 1))
	end
	return table.concat(parts, ":")
end

-- DUID-LL using iface MAC if possible
local mac = "\000\017\186\000\003\048"
local mf = io.open("/sys/class/net/" .. IFACE .. "/address")
if mf then
	local line = mf:read("*l") or ""
	mf:close()
	local bytes = {}
	for h in line:gmatch("%x%x") do
		bytes[#bytes + 1] = string.char(tonumber(h, 16))
	end
	if #bytes == 6 then
		mac = table.concat(bytes)
	end
end
local CLIENT_DUID = "\000\003\000\001" .. mac -- DUID-LL
local IAID = "\000\000\000\001"
local xid = string.char(math.random(0, 255), math.random(0, 255), math.random(0, 255))

local solicit = string.char(1) -- SOLICIT
	.. xid
	.. opt(1, CLIENT_DUID)
	.. opt(3, IAID .. u32(0) .. u32(0)) -- IA_NA empty
	.. opt(14, "") -- Rapid Commit
	.. opt(6, u16(23) .. u16(24)) -- ORO DNS

local s = assert(nixio.socket("inet6", "dgram"))
s:setsockopt("socket", "reuseaddr", 1)
pcall(function()
	s:setsockopt("ipv6", "v6only", 1)
end)
assert(s:bind("::", 546), "bind :546 failed")
pcall(function()
	s:setsockopt("socket", "bindtodevice", IFACE)
end)
pcall(function()
	s:setsockopt("ipv6", "multicast_hops", 1)
end)

local t0 = os.time()
log("sending SOLICIT+RapidCommit on " .. IFACE)
s:sendto(solicit, "ff02::1:2", 547)

local deadline = os.time() + TIMEOUT
local assigned = ""
local mtype_got = 0
while os.time() <= deadline do
	pcall(function()
		s:setopt("socket", "rcvtimeo", 1)
	end)
	local data, host, port = s:recvfrom(2048)
	if data and #data >= 4 then
		local mtype = data:byte(1)
		local rxid = data:sub(2, 4)
		if rxid == xid then
			mtype_got = mtype
			log(string.format("got msg=%d from=%s", mtype, tostring(host)))
			if mtype == 7 then -- REPLY
				local opts = find_opts(data, 5, #data)
				local iana = find_opt(opts, 3)
				if iana then
					local nested = find_opts(iana.data, 13, #iana.data) -- after iaid+t1+t2
					-- actually IA_NA body: iaid(4)+t1(4)+t2(4)=12, options from 13
					local start = 13
					if #iana.data >= 12 then
						nested = find_opts(iana.data, 13, #iana.data)
					else
						nested = {}
					end
					local iaaddr = find_opt(nested, 5)
					if iaaddr and #iaaddr.data >= 16 then
						assigned = inet_ntop6(iaaddr.data:sub(1, 16))
					end
				end
				local has_rc = find_opt(find_opts(data, 5, #data), 14) ~= nil
				local f = io.open(OUT, "w")
				if f then
					f:write("STATUS=OK\n")
					f:write("MSG=REPLY\n")
					f:write("ADDR=" .. tostring(assigned) .. "\n")
					f:write("RAPID_COMMIT=" .. (has_rc and "yes" or "no") .. "\n")
					f:write("ELAPSED_S=" .. tostring(os.time() - t0) .. "\n")
					f:close()
				end
				log("assigned=" .. tostring(assigned))
				os.exit(0)
			elseif mtype == 2 then
				log("got ADVERTISE (no rapid commit)")
			end
		end
	end
end

local f = io.open(OUT, "w")
if f then
	f:write("STATUS=FAIL\n")
	f:write("MSG=" .. tostring(mtype_got) .. "\n")
	f:close()
end
log("timeout")
os.exit(1)
