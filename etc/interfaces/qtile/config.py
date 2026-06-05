"""
TGW Qtile configuration — Trader Grim's Warehouse operator workstation.

X11 tiling WM with TGW API hooks baked in:
  - Status bar: live queue depth, worker health, clipboard SKU detector
  - Super+T chord: TGW command mode (health / queue / staged / todo / SKU action)
  - F12: scratchpad terminal (konsole)
  - 5 named workspaces: shell / tgw / ebay / agents / media

Install: etc/interfaces/qtile/install.sh
"""

import os
import re
import subprocess
import sys

from libqtile import bar, hook, layout, widget
from libqtile.config import (
    Click, Drag, DropDown, Group, Key, KeyChord, Match, Screen, ScratchPad,
)
from libqtile.lazy import lazy

# Pull in TGW widgets from the same directory as this config.
sys.path.insert(0, os.path.dirname(__file__))
from tgw_widgets import TGWHealthWidget, TGWQueueWidget, TGWSKUWidget  # noqa: E402

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

MOD = "mod4"          # Super / Windows key
TERMINAL = "konsole"  # KDE terminal — matches macroboard and existing tooling
LAUNCHER = "dmenu_run -fn 'monospace-11' -nb '#1a1a2e' -nf '#e0e0e0' -sb '#e94560' -sf '#1a1a2e'"
TGW_API_URL = "http://localhost:7373"
TGW_API_KEY_FILE = os.path.expanduser("~/.config/tgw/api-key")

# ---------------------------------------------------------------------------
# Colors — dark navy with TGW red accent
# ---------------------------------------------------------------------------

C = {
    "bg":       "#1a1a2e",
    "bg2":      "#16213e",
    "fg":       "#e0e0e0",
    "fg_dim":   "#888899",
    "accent":   "#e94560",   # TGW red
    "ok":       "#a8e6cf",
    "warn":     "#ffd3a5",
    "error":    "#ff6b6b",
    "border_focus":   "#e94560",
    "border_normal":  "#2d2d44",
}

# ---------------------------------------------------------------------------
# TGW action helpers (called via lazy.function)
# ---------------------------------------------------------------------------

def _tgw_term(cmd: str) -> str:
    """Wrap a command in a konsole that stays open after it finishes."""
    return f"{TERMINAL} -e bash -c '{cmd}; echo; read -rp \"[enter to close]\npress\n\"'"


def tgw_clipboard_action(qtile):
    """Super+T → c: look up SKU currently in clipboard."""
    for sel in ("primary", "clipboard"):
        try:
            result = subprocess.run(
                ["xclip", "-o", "-selection", sel],
                capture_output=True, text=True, timeout=1,
            )
            m = re.match(r"^(tgw\d{15})$", result.stdout.strip())
            if m:
                sku = m.group(1)
                qtile.spawn(_tgw_term(f"sudo -u tgw tgw lookup {sku}"))
                return
        except Exception:
            pass
    subprocess.run(["notify-send", "-t", "3000", "TGW", "No SKU in clipboard"])


# ---------------------------------------------------------------------------
# Groups — named workspaces + F12 scratchpad
# ---------------------------------------------------------------------------

groups = [
    Group("1", label="shell"),
    Group("2", label="tgw"),
    Group("3", label="ebay"),
    Group("4", label="agents"),
    Group("5", label="media"),
    ScratchPad("scratchpad", [
        DropDown(
            "tgw-shell",
            TERMINAL,
            opacity=0.92,
            height=0.55,
            width=0.85,
            x=0.075,
            y=0.05,
        ),
    ]),
]

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

keys = [
    # ── Focus ───────────────────────────────────────────────────────────────
    Key([MOD], "h", lazy.layout.left(),  desc="focus left"),
    Key([MOD], "l", lazy.layout.right(), desc="focus right"),
    Key([MOD], "j", lazy.layout.down(),  desc="focus down"),
    Key([MOD], "k", lazy.layout.up(),    desc="focus up"),

    # ── Move ────────────────────────────────────────────────────────────────
    Key([MOD, "shift"], "h", lazy.layout.shuffle_left()),
    Key([MOD, "shift"], "l", lazy.layout.shuffle_right()),
    Key([MOD, "shift"], "j", lazy.layout.shuffle_down()),
    Key([MOD, "shift"], "k", lazy.layout.shuffle_up()),

    # ── Resize ──────────────────────────────────────────────────────────────
    Key([MOD, "control"], "h", lazy.layout.grow_left()),
    Key([MOD, "control"], "l", lazy.layout.grow_right()),
    Key([MOD, "control"], "j", lazy.layout.grow_down()),
    Key([MOD, "control"], "k", lazy.layout.grow_up()),
    Key([MOD, "control"], "n", lazy.layout.normalize()),

    # ── Layout ──────────────────────────────────────────────────────────────
    Key([MOD], "Tab",          lazy.next_layout()),
    Key([MOD, "shift"], "Tab", lazy.prev_layout()),
    Key([MOD], "f",            lazy.window.toggle_fullscreen()),
    Key([MOD, "shift"], "f",   lazy.window.toggle_floating()),

    # ── Window ──────────────────────────────────────────────────────────────
    Key([MOD], "w",            lazy.window.kill()),
    Key([MOD, "control"], "r", lazy.reload_config(), desc="reload Qtile config"),
    Key([MOD, "control"], "q", lazy.shutdown()),

    # ── Launch ──────────────────────────────────────────────────────────────
    Key([MOD], "Return", lazy.spawn(TERMINAL)),
    Key([MOD], "d",      lazy.spawn(LAUNCHER)),
    Key([MOD], "r",      lazy.spawncmd()),

    # ── Scratchpad ──────────────────────────────────────────────────────────
    Key([], "F12", lazy.group["scratchpad"].dropdown_toggle("tgw-shell"),
        desc="Toggle TGW scratchpad terminal"),

    # ── TGW command chord: Super+T ──────────────────────────────────────────
    # Press Super+T to enter TGW mode; bar shows [TGW]; press Escape to exit.
    # Keys within TGW mode mirror the macroboard Caps Lock layer semantics.
    KeyChord(
        [MOD], "t",
        [
            # Info / status
            Key([], "h", lazy.spawn(_tgw_term("sudo -u tgw tgw health")),
                desc="health check"),
            Key([], "q", lazy.spawn(_tgw_term(
                "sudo -u tgw psql -U tgw state_machine -c "
                "\"SELECT queue_name, state, count(*) FROM queue_jobs "
                " WHERE state != 'completed' "
                " GROUP BY queue_name, state ORDER BY queue_name, state;\""
            )), desc="queue depths"),
            Key([], "s", lazy.spawn(_tgw_term("sudo -u tgw tgw staged")),
                desc="staged items"),
            Key([], "t", lazy.spawn(_tgw_term("sudo -u tgw tgw todo")),
                desc="todo list"),
            Key([], "v", lazy.spawn(_tgw_term("sudo -u tgw tgw velocity-report")),
                desc="velocity report"),

            # Clipboard SKU action
            Key([], "c", lazy.function(tgw_clipboard_action),
                desc="clipboard SKU → lookup"),

            # Pipeline triggers (operate on clipboard/CurrentItem SKU)
            Key([], "1", lazy.spawn(_tgw_term(
                "SKU=$(xclip -o -selection primary 2>/dev/null || basename $(readlink /opt/TGW/CurrentItem 2>/dev/null)); "
                "echo SKU: $SKU; sudo -u tgw tgw requeue-sku $SKU ai_identify 2>/dev/null || "
                "echo 'usage: set SKU in primary selection first'"
            )), desc="queue ai_identify for SKU"),
            Key([], "2", lazy.spawn(_tgw_term(
                "SKU=$(xclip -o -selection primary 2>/dev/null || basename $(readlink /opt/TGW/CurrentItem 2>/dev/null)); "
                "echo SKU: $SKU; sudo -u tgw tgw requeue-sku $SKU ebay_draft 2>/dev/null || "
                "echo 'usage: set SKU in primary selection first'"
            )), desc="queue ebay_draft for SKU"),

            # Workspace jump
            Key([], "F2", lazy.group["2"].toscreen(), desc="jump to tgw workspace"),
            Key([], "F4", lazy.group["4"].toscreen(), desc="jump to agents workspace"),

            # Open TGW folder in Dolphin
            Key([], "o", lazy.spawn("dolphin /opt/TGW/data/ItemData"),
                desc="open ItemData in Dolphin"),

            Key([], "Escape", lazy.ungrab_all_chords(), desc="exit TGW mode"),
        ],
        mode=True,
        name="TGW",
    ),
]

# Workspace navigation
for i, grp in enumerate(groups[:5], 1):
    keys += [
        Key([MOD], str(i), lazy.group[grp.name].toscreen()),
        Key([MOD, "shift"], str(i), lazy.window.togroup(grp.name, switch_group=True)),
    ]

# Mouse bindings
mouse = [
    Drag([MOD], "Button1", lazy.window.set_position_floating(), start=lazy.window.get_position()),
    Drag([MOD], "Button3", lazy.window.set_size_floating(),     start=lazy.window.get_size()),
    Click([MOD], "Button2", lazy.window.bring_to_front()),
]

# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

_L = {
    "border_width": 2,
    "margin": 6,
    "border_focus":  C["border_focus"],
    "border_normal": C["border_normal"],
}

layouts = [
    layout.MonadTall(**_L, ratio=0.55),
    layout.MonadWide(**_L),
    layout.Columns(**_L, num_columns=3),
    layout.Max(**_L),
]

floating_layout = layout.Floating(
    border_focus=C["border_focus"],
    border_normal=C["border_normal"],
    border_width=2,
    float_rules=[
        *layout.Floating.default_float_rules,
        Match(wm_class="kdialog"),
        Match(wm_class="ksplash"),
        Match(wm_class="systemsettings"),
        Match(wm_class="plasmashell"),
        Match(title="Confirm"),
        Match(title="Open"),
    ],
)

# ---------------------------------------------------------------------------
# Bar
# ---------------------------------------------------------------------------

def _sep():
    return widget.Sep(linewidth=1, padding=8, foreground=C["fg_dim"])


screens = [
    Screen(
        top=bar.Bar(
            [
                # Left: workspace, layout, window name
                widget.GroupBox(
                    active=C["fg"],
                    inactive=C["fg_dim"],
                    highlight_method="block",
                    this_current_screen_border=C["accent"],
                    this_screen_border=C["bg2"],
                    urgent_border=C["error"],
                    rounded=False,
                    fontsize=12,
                    padding=5,
                    margin_x=2,
                    background=C["bg"],
                ),
                _sep(),
                widget.CurrentLayout(
                    foreground=C["accent"],
                    fontsize=11,
                ),
                _sep(),
                widget.Prompt(
                    foreground=C["accent"],
                    prompt="run: ",
                ),
                widget.WindowName(
                    foreground=C["fg"],
                    max_chars=60,
                ),

                # Chord mode indicator (shows "[TGW]" when Super+T is active)
                widget.Chord(
                    chords_colors={"TGW": (C["accent"], C["bg"])},
                    name_transform=lambda name: f"[ {name} ]",
                    foreground=C["accent"],
                ),

                # Right: TGW live widgets
                _sep(),
                TGWSKUWidget(
                    update_interval=2,
                    markup=True,
                    color_idle=C["fg_dim"],
                    color_sku=C["accent"],
                    placeholder="—",
                    fontsize=11,
                    padding=4,
                    mouse_callbacks={
                        "Button1": lazy.function(tgw_clipboard_action),
                    },
                ),
                _sep(),
                TGWQueueWidget(
                    update_interval=30,
                    markup=True,
                    api_url=TGW_API_URL,
                    api_key_file=TGW_API_KEY_FILE,
                    color_ok=C["ok"],
                    color_busy=C["warn"],
                    color_error=C["error"],
                    fontsize=11,
                    padding=4,
                    mouse_callbacks={
                        "Button1": lazy.spawn(_tgw_term("sudo -u tgw tgw health")),
                    },
                ),
                _sep(),
                TGWHealthWidget(
                    update_interval=60,
                    markup=True,
                    color_ok=C["ok"],
                    color_warn=C["warn"],
                    fontsize=11,
                    padding=4,
                    mouse_callbacks={
                        "Button1": lazy.spawn(_tgw_term(
                            "systemctl list-units 'tgw-worker@*' tgw-http.service"
                        )),
                    },
                ),
                _sep(),
                widget.Systray(padding=4),
                _sep(),
                widget.Clock(
                    format="%a %b %-d  %H:%M",
                    foreground=C["fg"],
                    fontsize=11,
                    padding=6,
                ),
            ],
            28,
            background=C["bg"],
            opacity=0.95,
            margin=[0, 0, 2, 0],
        ),
    ),
]

# ---------------------------------------------------------------------------
# Global Qtile settings
# ---------------------------------------------------------------------------

dgroups_key_binder = None
dgroups_app_rules = []
follow_mouse_focus = True
bring_front_click = "floating_only"
cursor_warp = False
auto_fullscreen = True
focus_on_window_activation = "smart"
reconfigure_screens = True
auto_minimize = True
wl_input_rules = None   # X11 mode — Wayland not used
wmname = "LG3D"         # JVM compatibility

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

@hook.subscribe.startup_once
def autostart():
    """Run once when Qtile first starts."""
    home = os.path.expanduser("~")
    autostart_script = os.path.join(home, ".config", "qtile", "autostart.sh")
    if os.path.isfile(autostart_script):
        subprocess.Popen(["bash", autostart_script])
