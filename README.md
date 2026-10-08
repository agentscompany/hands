# driver

The computer-use driver of [AgentsCompany](https://agentscompany.ai) bots and [Desks](https://github.com/agentscompany)
agents: a Linux desktop (X on display `:1`) as windows, elements and text, so an agent acts by element rather than by
pixel. It follows the idea of the [Cua Driver](https://cua.ai): list the windows, read one window as an accessibility
tree plus a screenshot, then act on its elements by number.

- `desk-a11y`: windows (`wmctrl`, `xdotool`) and any GTK app's elements through AT-SPI, the Linux accessibility bus.
- `pc-ui`: Chromium pages as text and actions, through Chromium's debugging protocol.
- `pc`: the commands, plus pixels (screenshot, click, type, key, scroll) and opening apps. `desk` is `pc` for one agent.

Used by [AgentOS](https://github.com/agentscompany/agentos), which installs a tagged release (its CI checks this
repository out with a read-only deploy key).

## Install

On Debian 13 with a desktop on `:1`, as root (e.g. in a Dockerfile), from a checkout of a release tag:

    git clone --depth 1 --branch v0.1.0 git@github.com:agentscompany/driver.git && sh driver/install.sh

Chromium must run with `--remote-debugging-port=0` for its pages to read as text.

## Use it in this order

1. `desk windows`: the windows, numbered.
2. `desk state <window>`: that window's elements, numbered, and a screenshot of just that window. `<window>` is a
   number from `desk windows` or a word of its app or title (`terminal`, `files`, `chromium`).
3. Act on the numbers of the last `state`: `desk press N`, `desk set N "text"`, `desk text`; and on windows:
   `desk close|focus|max|min <window>`. Run `state` again after the window changes.

A screenshot is for checking, or for when the elements do not help (`desk screenshot`, `desk click X Y`).

    $ desk windows
    [1] xfce4-terminal "Terminal - ac@desk: ~" 772x477
    [2] thunar "ac - Thunar" 900x560
    [3] chromium "Example Domain - Chromium" 1177x648 (focused)

    $ desk state files
    thunar "ac - Thunar" 900x560 at 190,159; screenshot of this window: /home/ac/.desk/screens/alfred-20261008-201651-thunar.png
    [1] button "Back"
    [4] button "Home"
    [5] text "" = "/home/ac/"
    [10] menu "View"
    [17] table cell "File System"
    # 10 folders | 5 files: 5.0 KiB (5114 bytes) | Free space: 1.6 TiB
    [22] table cell "Projects"
    [25] table cell "notes.txt"

    $ desk press 25 && desk key Return      # a row is selected by press; Return opens it
    $ desk state terminal && desk set 1 "ls" && desk key Return && desk text
    $ desk close chromium

Actions use the element's accessible action when the app has one (the window need not be in front); otherwise they
focus the window and click the element's center. Either way the pointer moves to the element, so whoever watches the
screen sees where the agent acted.

## Commands

    desk windows | state W | press N | set N text | text | close|focus|max|min W
    desk ui | upload N file                       the agent's own Chromium page (pages need no state first)
    desk open url|terminal|files                  url: in the agent's own Chromium profile
    desk screenshot [file] | click X Y | move X Y | type text | key ctrl+l | scroll X Y N

`desk` takes the agent from `DESK_AGENT` (its Chromium profile and its own numbering), keeps screenshots in
`~/.desk/screens` (the last 20; `DESK_SCREENS` changes it) and writes `~/.desk/last` (`<agent> <unix time>`), so a
viewer knows who is using the screen. An `xdotool` shim in `/usr/local/bin` writes it too when an agent (anything with
`DESK_AGENT` set) runs xdotool directly, then runs the real one.
`pc` has the same element commands with `--as <profile>` right after the command (`pc state --as alfred files`), plus `double`, `move`,
`open --as <profile> #rrggbb [url]` (a Chromium profile in a color theme) and `tint #rrggbb` (the desktop's color).

## Test

    python3 test_desk_a11y.py      # the window list and the numbering of the tree; no desktop needed

MIT License.
