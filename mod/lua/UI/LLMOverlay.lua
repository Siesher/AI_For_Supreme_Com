-- T050: UI Debug Overlay — mod/lua/UI/LLMOverlay.lua
--
-- FAF UI layer panel. Toggle with Ctrl+Shift+D.
-- Displays:
--   - Current strategy name
--   - Last reasoning (200 chars)
--   - Active model (4B / 8B)
--   - LLM connection status
--   - Countdown to next query
--   - Reflex layer status (last action + when)
--
-- Reads data from UserSync shared table updated by the sim layer.
-- This file runs in the UI layer (not sim); uses FAF UI API.

local UIUtil     = import("/lua/ui/uiutil.lua")
local LayoutHelpers = import("/lua/maui/layouthelpers.lua")
local Group      = import("/lua/maui/group.lua").Group
local Bitmap     = import("/lua/maui/bitmap.lua").Bitmap
local Text       = import("/lua/maui/text.lua").Text
local Window     = import("/lua/maui/window.lua").Window

-- ---------------------------------------------------------------------------
-- State
-- ---------------------------------------------------------------------------

local _overlay_visible = false
local _overlay_panel   = nil
local _labels          = {}

local PANEL_W = 340
local PANEL_H = 180

-- ---------------------------------------------------------------------------
-- Data source: UserSync table (written from sim layer via UserSync.SyncTable)
-- ---------------------------------------------------------------------------

local function GetOverlayData()
    -- Data written by LLMAIBrain sim side via UserSync
    -- Fallback to empty defaults if not yet populated
    local sync = UserSync and UserSync.LLMOverlay or {}
    return {
        strategy      = sync.strategy    or "—",
        reasoning     = (sync.reasoning  or ""):sub(1, 200),
        model         = sync.model       or "—",
        status        = sync.status      or "disconnected",
        next_query_s  = sync.next_query_s or 0,
        reflex_action = sync.reflex_action or "none",
        reflex_tick   = sync.reflex_tick   or 0,
        fallback      = sync.fallback      or false,
    }
end

-- ---------------------------------------------------------------------------
-- Panel construction
-- ---------------------------------------------------------------------------

local function CreateOverlayPanel(parent)
    local panel = Window(parent, "LLM AI Debug", false, false, false,
                         false, false, "llm_overlay")
    LayoutHelpers.SetDimensions(panel, PANEL_W, PANEL_H)
    LayoutHelpers.AtRightTopIn(panel, parent, 10, 60)

    -- Background bitmap (semi-transparent dark)
    local bg = Bitmap(panel)
    bg:SetSolidColor("CC000000")
    LayoutHelpers.FillParent(bg, panel)

    local y = 8
    local function AddLabel(key, text)
        local lbl = UIUtil.CreateText(panel, text, 11, UIUtil.bodyFont)
        LayoutHelpers.AtLeftTopIn(lbl, panel, 8, y)
        _labels[key] = lbl
        y = y + 16
        return lbl
    end

    -- Title
    local title = UIUtil.CreateText(panel, "[ LLM AI Bot Debug ]", 12, UIUtil.titleFont)
    LayoutHelpers.AtLeftTopIn(title, panel, 8, y)
    title:SetColor("FF00CCFF")
    y = y + 18

    AddLabel("status",       "Status: —")
    AddLabel("model",        "Model:  —")
    AddLabel("strategy",     "Strategy: —")
    AddLabel("next_query",   "Next query: —s")
    AddLabel("reflex",       "Reflex: none")
    AddLabel("reasoning",    "Reasoning: —")

    return panel
end

local function UpdateOverlayLabels()
    if not _overlay_visible or not _overlay_panel then return end
    local d = GetOverlayData()

    local status_color = (d.status == "connected") and "FF00FF00"
                      or (d.status == "connecting") and "FFFFFF00"
                      or "FFFF4444"

    local function SetLabel(key, text, color)
        if _labels[key] then
            _labels[key]:SetText(text)
            if color then _labels[key]:SetColor(color) end
        end
    end

    SetLabel("status",    "Status: " .. d.status,                     status_color)
    SetLabel("model",     "Model: "  .. d.model)
    SetLabel("strategy",  "Strategy: " .. d.strategy
                          .. (d.fallback and " [FALLBACK]" or ""),
                          d.fallback and "FFFFFF44" or "FFFFFFFF")
    SetLabel("next_query","Next query: " .. tostring(d.next_query_s) .. "s")

    local reflex_text = d.reflex_action
    if d.reflex_tick > 0 then
        reflex_text = reflex_text .. " (tick " .. d.reflex_tick .. ")"
    end
    SetLabel("reflex",    "Reflex: " .. reflex_text,
             d.reflex_action ~= "none" and "FF00FFCC" or "FF888888")
    SetLabel("reasoning", "Reason: " .. d.reasoning)
end

-- ---------------------------------------------------------------------------
-- Toggle
-- ---------------------------------------------------------------------------

local function ToggleOverlay(worldView)
    _overlay_visible = not _overlay_visible
    if _overlay_visible then
        if not _overlay_panel then
            _overlay_panel = CreateOverlayPanel(worldView)
        end
        _overlay_panel:Show()
        -- Start update timer
        ForkThread(function()
            while _overlay_visible do
                UpdateOverlayLabels()
                WaitSeconds(1)
            end
        end)
    else
        if _overlay_panel then
            _overlay_panel:Hide()
        end
    end
end

-- ---------------------------------------------------------------------------
-- Key binding — Ctrl+Shift+D
-- ---------------------------------------------------------------------------

local hooked = false

local function HookKeyPress(worldView)
    if hooked then return end
    hooked = true
    import("/lua/ui/game/worldview.lua").RegisterWorldViewKeyHandler(
        "Toggle LLM Overlay",
        function(key, modifiers)
            if key == "D" and modifiers.Ctrl and modifiers.Shift then
                ToggleOverlay(worldView)
                return true
            end
        end
    )
end

-- ---------------------------------------------------------------------------
-- Entry point — called by FAF when the UI mod initialises
-- ---------------------------------------------------------------------------

function Init(isReplay)
    -- Hook into the game world view to capture key presses
    -- FAF UI mods usually hook via OnInit or by registering callbacks
    local worldView = import("/lua/ui/game/worldview.lua").GetWorldView()
    if worldView then
        HookKeyPress(worldView)
    end

    -- Register UserSync handler to receive data from sim layer
    if AddChatlineHook then
        -- FAF-specific: listen for UserSync.LLMOverlay updates
        -- (Actual FAF hook mechanism depends on version — this is a placeholder)
    end

    LOG("[LLMOverlay] Initialised. Press Ctrl+Shift+D to toggle overlay.")
end
