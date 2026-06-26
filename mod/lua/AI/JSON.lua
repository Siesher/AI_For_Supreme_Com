-- JSON.lua — minimal pure-Lua JSON encoder/decoder for SupCom Lua 5.0
-- Provides: encode(value) -> JSON string
--           decode(str)   -> Lua value, or nil + error message

local function encode_string(s)
    s = string.gsub(s, '\\', '\\\\')
    s = string.gsub(s, '"',  '\\"')
    s = string.gsub(s, '\n', '\\n')
    s = string.gsub(s, '\r', '\\r')
    s = string.gsub(s, '\t', '\\t')
    return '"' .. s .. '"'
end

local function encode_value(val)
    local t = type(val)

    if t == "nil" then
        return "null"

    elseif t == "boolean" then
        return val and "true" or "false"

    elseif t == "number" then
        -- NaN check: NaN is not equal to itself
        if val ~= val then return "0" end
        -- Just use tostring — SupCom Lua 5.0 handles it. Avoid math.floor
        -- which may behave unexpectedly with certain engine-returned numbers.
        local s = tostring(val)
        -- tostring may return "inf"/"-inf"/"nan" strings which aren't valid JSON
        if s == "inf" or s == "-inf" or s == "nan" or s == "1.#INF" or s == "-1.#INF" then
            return "0"
        end
        return s

    elseif t == "string" then
        return encode_string(val)

    elseif t == "table" then
        -- Detect whether this is a sequence (array) or a hash (object).
        -- table.getn returns the length of the integer-key sequence part.
        local n = table.getn(val)
        local is_array = (n > 0)

        if is_array then
            local parts = {}
            for i = 1, n do
                -- Per-value pcall: skip/null-out items that can't be encoded
                local ok, enc = pcall(encode_value, val[i])
                table.insert(parts, ok and enc or "null")
            end
            return '[' .. table.concat(parts, ',') .. ']'
        else
            -- Object: iterate all string keys
            local parts = {}
            for k, v in pairs(val) do
                if type(k) == "string" then
                    local ok, enc = pcall(encode_value, v)
                    if ok then
                        table.insert(parts, encode_string(k) .. ':' .. enc)
                    else
                        table.insert(parts, encode_string(k) .. ':null')
                    end
                end
            end
            if table.getn(parts) == 0 then
                return '{}'
            end
            return '{' .. table.concat(parts, ',') .. '}'
        end

    else
        -- userdata, function, thread -> null
        return "null"
    end
end

---Encode a Lua value to a JSON string.
---@param val any
---@return string
function encode(val)
    local ok, result = pcall(encode_value, val)
    if ok then
        return result
    end
    return '{"error":"json_encode_failed"}'
end

-- ---------------------------------------------------------------------------
-- Minimal JSON decoder — handles objects, arrays, strings, numbers, booleans.
-- Lua 5.0 compatible (no # operator, no goto, no string.format %q).
-- ---------------------------------------------------------------------------

local function skip_ws(s, i)
    while i <= string.len(s) do
        local c = string.sub(s, i, i)
        if c == ' ' or c == '\t' or c == '\n' or c == '\r' then
            i = i + 1
        else
            break
        end
    end
    return i
end

local decode_value  -- forward declaration (assigned below after helper functions)

local function decode_string(s, i)
    -- i points at the opening "
    local result = {}
    i = i + 1  -- skip "
    while i <= string.len(s) do
        local c = string.sub(s, i, i)
        if c == '"' then
            return table.concat(result), i + 1
        elseif c == '\\' then
            local e = string.sub(s, i + 1, i + 1)
            if     e == '"'  then table.insert(result, '"')
            elseif e == '\\' then table.insert(result, '\\')
            elseif e == '/'  then table.insert(result, '/')
            elseif e == 'n'  then table.insert(result, '\n')
            elseif e == 'r'  then table.insert(result, '\r')
            elseif e == 't'  then table.insert(result, '\t')
            else                   table.insert(result, e)
            end
            i = i + 2
        else
            table.insert(result, c)
            i = i + 1
        end
    end
    return nil, "unterminated string"
end

local function decode_number(s, i)
    local j = i
    if string.sub(s, j, j) == '-' then j = j + 1 end
    while j <= string.len(s) do
        local c = string.sub(s, j, j)
        if c >= '0' and c <= '9' or c == '.' or c == 'e' or c == 'E' or c == '+' or c == '-' then
            j = j + 1
        else
            break
        end
    end
    local num = tonumber(string.sub(s, i, j - 1))
    if num == nil then return nil, "bad number at " .. i end
    return num, j
end

local function decode_array(s, i)
    local arr = {}
    i = i + 1  -- skip [
    i = skip_ws(s, i)
    if string.sub(s, i, i) == ']' then return arr, i + 1 end
    while true do
        local val, ni = decode_value(s, i)
        if type(ni) ~= "number" then return nil, tostring(ni) end
        table.insert(arr, val)
        i = skip_ws(s, ni)
        local c = string.sub(s, i, i)
        if c == ']' then return arr, i + 1 end
        if c ~= ',' then return nil, "expected , or ] in array" end
        i = skip_ws(s, i + 1)
    end
end

local function decode_object(s, i)
    local obj = {}
    i = i + 1  -- skip {
    i = skip_ws(s, i)
    if string.sub(s, i, i) == '}' then return obj, i + 1 end
    while true do
        i = skip_ws(s, i)
        if string.sub(s, i, i) ~= '"' then return nil, "expected string key" end
        local key, ni = decode_string(s, i)
        if type(ni) ~= "number" then return nil, tostring(ni) end
        i = skip_ws(s, ni)
        if string.sub(s, i, i) ~= ':' then return nil, "expected : after key" end
        i = skip_ws(s, i + 1)
        local val, vi = decode_value(s, i)
        if type(vi) ~= "number" then return nil, tostring(vi) end
        obj[key] = val
        i = skip_ws(s, vi)
        local c = string.sub(s, i, i)
        if c == '}' then return obj, i + 1 end
        if c ~= ',' then return nil, "expected , or } in object" end
        i = skip_ws(s, i + 1)
    end
end

decode_value = function(s, i)
    i = skip_ws(s, i)
    local c = string.sub(s, i, i)
    if c == '"' then
        return decode_string(s, i)
    elseif c == '[' then
        return decode_array(s, i)
    elseif c == '{' then
        return decode_object(s, i)
    elseif c == 't' then
        if string.sub(s, i, i + 3) == 'true' then return true, i + 4 end
        return nil, "bad token at " .. i
    elseif c == 'f' then
        if string.sub(s, i, i + 4) == 'false' then return false, i + 5 end
        return nil, "bad token at " .. i
    elseif c == 'n' then
        if string.sub(s, i, i + 3) == 'null' then return nil, i + 4 end
        return nil, "bad token at " .. i
    elseif c == '-' or (c >= '0' and c <= '9') then
        return decode_number(s, i)
    else
        return nil, "unexpected char '" .. c .. "' at " .. i
    end
end

---Decode a JSON string to a Lua value.
---Returns (value) on success, or (nil, error_string) on failure.
function decode(s)
    if type(s) ~= "string" then
        return nil, "expected string"
    end
    local ok, val, ni = pcall(decode_value, s, 1)
    if not ok then
        return nil, tostring(val)  -- decode_value threw an unexpected error
    end
    if type(ni) ~= "number" then
        return nil, tostring(ni)   -- ni is an error string, val is nil
    end
    return val
end
