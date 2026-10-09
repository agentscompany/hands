# Hands: computer use for agents

Hands lets an agent use a Linux desktop (X on display `:1`) through windows, elements and text, acting on an element
rather than a pixel. It follows the idea of the [Cua Driver](https://cua.ai): list the windows, read one window as an
accessibility tree plus a screenshot, then act on its elements by number. It is the hands of
[AgentsCompany](https://agentscompany.ai) bots and of [Desks](https://github.com/agentscompany) agents.

- `hands`: the command.
- `hands-a11y`: windows (`wmctrl`, `xdotool`) and any GTK app's elements through AT-SPI, the Linux accessibility bus.
- `pc-ui`: Chromium pages as text and actions, through Chromium's debugging protocol.
- `pc` and `desk`: the old names of `hands`, kept so existing agents and the AgentsCompany daemon keep working.

Used by [AgentOS](https://github.com/agentscompany/agentos), which installs a tagged release (its CI checks this
repository out with a read-only deploy key).

## Install

On Debian 13 with a desktop on `:1`, as root (e.g. in a Dockerfile), from a checkout of a release tag:

    git clone --depth 1 --branch v0.2.0 git@github.com:agentscompany/hands.git && sh hands/install.sh

Chromium must run with `--remote-debugging-port=0` for its pages to read as text.

## Use it in this order

1. `hands windows`: the windows, numbered.
2. `hands state <window>`: that window's elements, numbered, and a screenshot of just that window. `<window>` is a
   number from `hands windows` or a word of its app or title (`terminal`, `files`, `chromium`).
3. Act on the numbers of the last `state`: `hands press N`, `hands set N "text"`, `hands text`; and on windows:
   `hands close|focus|max|min <window>`. Run `state` again after the window changes.

A screenshot is for checking, or for when the elements do not help (`hands screenshot`, `hands click X Y`).

    $ hands windows
    [1] xfce4-terminal "Terminal - ac@desk: ~" 772x477
    [2] thunar "ac - Thunar" 900x560
    [3] chromium "Example Domain - Chromium" 1177x648 (focused)

    $ hands state files
    thunar "ac - Thunar" 900x560 at 190,159; screenshot of this window: /home/ac/.desk/screens/alfred-20261008-201651-thunar.png
    [1] button "Back"
    [4] button "Home"
    [5] text "" = "/home/ac/"
    [10] menu "View"
    [17] table cell "File System"
    # 10 folders | 5 files: 5.0 KiB (5114 bytes) | Free space: 1.6 TiB
    [22] table cell "Projects"
    [25] table cell "notes.txt"

    $ hands press 25 && hands key Return      # a row is selected by press; Return opens it
    $ hands state terminal && hands set 1 "ls" && hands key Return && hands text
    $ hands close chromium

Actions use the element's accessible action when the app has one (the window need not be in front); otherwise they
focus the window and click the element's center. Either way the pointer moves to the element, so whoever watches the
screen sees where the agent acted.

## Commands

    hands windows | state W | press N | set N text | text | close|focus|max|min W
    hands ui | upload N file                      the agent's own Chromium page (pages need no state first)
    hands open url|terminal|files|browser         url: in the agent's own Chromium profile
    hands screenshot [file] | click X Y | double X Y | move X Y | type text | key ctrl+l | scroll X Y N | tint #rrggbb

The agent is `DESK_AGENT`, or `--as <profile>` right after the command (`hands state --as alfred files`;
`hands open --as alfred #fb9b50 [url]` also gives its Chromium a color theme). It has its own Chromium profile and its
own numbering of elements. Screenshots go to `~/.desk/screens` (the last 20; `DESK_SCREENS` changes it). Every action
of a named agent writes `~/.desk/last` (`<agent> <unix time>`), so a viewer knows who is using the screen; an `xdotool`
shim in `/usr/local/bin` writes it too when an agent runs xdotool directly, then runs the real one.

## Test

    python3 test_hands_a11y.py      # the window list and the numbering of the tree; no desktop needed

MIT License.
