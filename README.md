# Hands: computer use for agents

Hands lets an agent use a Linux desktop (X on display `:1`) through windows, elements and text, acting on an element
rather than a pixel. It follows the idea of the [Cua Driver](https://cua.ai): list the windows, read one window as an
accessibility tree (a screenshot only when the elements do not help), then act on its elements by number. It is the hands of
[AgentsCompany](https://agentscompany.ai) bots and of [Desks](https://github.com/agentscompany) agents.

- `hands`: the command. Pixel actions and opening apps run in it; everything else goes to `handsd`.
- `handsd`: a daemon per user and desktop, started by the first command, and the thin client `hands` talks to over a
  Unix socket. It keeps the connections to the browsers and to AT-SPI open, listens to their events (dialogs, loading,
  downloads, requests; AT-SPI changes) and keeps each agent's session, so most commands answer in 10 to 25 ms. If it
  cannot start, the same commands run directly (slower, and without the events).
- `hands_a11y`: windows (`wmctrl`, `xdotool`) and any GTK app's elements through AT-SPI, the Linux accessibility bus;
  stable element numbers and change lists.
- `hands_cdp`: Chromium pages and Electron apps through their DevTools protocol (CDP).
- `pc` and `desk`: the old names of `hands`, kept so existing agents and the AgentsCompany daemon keep working.

Used by [AgentOS](https://github.com/agentscompany/agentos), which installs a tagged release (its CI checks this
repository out with a read-only deploy key).

## Install

On Debian 13 with a desktop on `:1`, as root (e.g. in a Dockerfile), from a checkout of a release tag:

    git clone --depth 1 --branch v0.4.0 git@github.com:agentscompany/hands.git && sh hands/install.sh

Chromium must run with `--remote-debugging-port=0` for its pages to read as text.

## Use it in this order

1. `hands windows`: the windows, numbered.
2. `hands state <window>`: that window's elements, numbered. `<window>` is a number from `hands windows` or a word of
   its app or title (`terminal`, `files`, `chromium`, `code`). Chromium windows and Electron apps show their page.
3. Act on the numbers of the last `state`: `hands press N`, `hands set N "text"`, `hands text`; and on windows:
   `hands close|focus|max|min <window>`.
4. After acting, `hands state <window> --changes` lists only what changed since your last state of that window
   (`+` new, `~` changed, `-` gone), or `(no changes)`. An element keeps its number while it lives, so the numbers
   you already have stay good.

Pixels are for canvas apps, or for checking: `hands state W --shot` adds a screenshot of the window (a window with no
elements gets one anyway), and `hands screenshot`, `hands click X Y` (these wait their turn on the real pointer).

    $ hands windows
    [1] xfce4-terminal "Terminal - ac@desk: ~" 772x477
    [2] thunar "ac - Thunar" 900x560
    [3] chromium "Example Domain - Chromium" 1177x648 (focused)

    $ hands state files
    thunar "ac - Thunar" 900x560 at 190,159
    [1] button "Back"
    [3] button "Open Parent"
    [4] button "Home"
    [5] text "" = "/home/ac/"
    [10] menu "View"
    # 7 folders | 3 files: 4.4 KiB (4553 bytes) | Free space: 1.6 TiB

    $ hands press 3 && hands state files --changes
    ok
    thunar "home - Thunar" 900x560 at 190,159
    ~ [5] text "" = "/home/"
    + # "ac" | Folder
    - # 7 folders | 3 files: 4.4 KiB (4553 bytes) | Free space: 1.6 TiB

Actions use the element's accessible action when the app has one (the window need not be in front); otherwise they
bring the window up and click the element's center with the real pointer, in the agent's turn (see below).

## Several agents at once

X has one pointer and one keyboard, so Hands only uses them when nothing else works, one agent at a time:

- Page actions (`press`, `set`, `fill`, `type`, `key`, `scroll N` on an agent's page or an Electron app) go through
  the page's own input (CDP), and accessible actions through AT-SPI: no real pointer, no keyboard focus, no raised
  window. Agents do these in parallel, each in its own browser.
- What needs the real pointer or keyboard takes a turn: `click`, `double`, `move`, `scroll X Y N`, `type` and `key`
  into a window that is not a page, a GTK element with no accessible action, `close|focus|max|min`, and `state --shot`
  (the window must be on top). Turns go in order of arrival and last one action; one waits at most 20 s, then fails
  with a clear message. The `xdotool` shim takes a turn too when `DESK_AGENT` is set.
- While the person watching has taken over (their screen page keeps `~/.desk/control` fresh), no turn is given;
  page commands still work.
- `type` and `key` go to the agent's page after `ui`, `press` or `set` on it, and to the screen after `click`, `focus`
  or `state` of another window. Keys for a page are the page's: browser shortcuts such as `ctrl+l` are not (use
  `hands open` and `hands tab`).

Each agent has a virtual cursor: where it acts, in screen pixels, and whether it is acting now, in
`~/.desk/cursors.json` (`{"alfred": {"x": 556, "y": 162, "acting": true, "t": 1791657099.15}}`, `t` its last
command). An action waits 0.4 s after its cursor moves, so a viewer that draws the cursors sees each one arrive before
it acts.

## The browser

Each agent has its own Chromium profile. Its page reads and acts without a `state` first:

    hands open https://example.com          the page in the agent's own Chromium
    hands ui [--changes]                    the page's elements, numbered (then press, set, upload by number)
    hands read [P]                          the page as light markdown (the main content when the page marks it),
                                            part P of a long one; hands text is all the page's text
    hands wait load | idle | text "Saved" [S]   until it loaded, the network was quiet for half a second, or the
                                            text shows (S: timeout in seconds, 10 by default)
    hands fill '{"Email": "a@b.c", "Plan": "Pro", "I agree": true, "7": "x"}'
                                            a form in one call: keys are labels, placeholders, names or numbers
    hands tabs | tab new [URL] | tab close [N] | tab N
    hands dialog ok|cancel [text]           answer an alert, confirm or prompt (ui and press tell when one is open)
    hands eval "JS"                         run JavaScript in the page and print the result. Powerful: it reads and
                                            does anything the page can, with the agent's logins. Use it with care.
    hands downloads                         the downloads seen since Hands connected, with where they were saved
    hands network                           the tab's recent requests (status, method, type, URL)
    hands pdf FILE                          the page as a PDF

## Electron apps

`hands open app <name>` opens an Electron app (VS Code, Slack, Discord, Obsidian, Notion…) with its DevTools port
open on 127.0.0.1; the port is on its command line, where Hands finds it. Then `hands state <app>` shows its elements
and `press`, `set`, `read`, `wait`, `eval` work on it as on a page. Any Chromium or Electron app started with
`--remote-debugging-port` works the same. If the app was already open without the port, close it first.

The apps are not in the image: install them when needed, e.g. VS Code on Debian (amd64 or arm64):

    curl -fsSLo /tmp/code.deb "https://update.code.visualstudio.com/latest/linux-deb-$(dpkg --print-architecture)/stable"
    sudo apt-get install -y /tmp/code.deb && hands open app code

Slack, Discord and Obsidian ship `.deb` packages the same way (`hands open app slack`); for an AppImage, put it in
the PATH under the name you will open it by.

## Commands

    hands windows | state W [--changes] [--shot] | press N | set N text | text | close|focus|max|min W
    hands ui [--changes] | read [P] | upload N file | fill JSON | wait load|idle|text T [S] | tabs | tab …
    hands dialog ok|cancel [text] | eval JS | downloads | network | pdf FILE
    hands open url|terminal|files|browser|app NAME   url: in the agent's own Chromium profile
    hands type text | key ctrl+a Return | scroll N    into the agent's page (see Several agents at once), or the screen
    hands screenshot [file] | click X Y | double X Y | move X Y | scroll X Y N | tint #rrggbb
    hands version                                    "(daemon)" when the daemon answered

The agent is `DESK_AGENT`, or `--as <profile>` right after the command (`hands state --as alfred files`;
`hands open --as alfred #fb9b50 [url]` also gives its Chromium a color theme). It has its own Chromium profile and its
own numbering of elements. Screenshots go to `~/.desk/screens` (the last 20; `DESK_SCREENS` changes it). Every action
of a named agent writes `~/.desk/last` (`<agent> <unix time>`), so a viewer knows who is using the screen; an `xdotool`
shim in `/usr/local/bin` writes it too when an agent runs xdotool directly, then runs the real one.

The daemon's socket is `$XDG_RUNTIME_DIR/hands.sock` (or `/tmp/hands-<uid>/hands.sock`, a directory only that user
can open); its log is `~/.desk/handsd.log`. It restarts by itself after Hands is updated.

## Test

    python3 test_hands.py      # windows, the tree's numbering, stable numbers, change lists, keys, turns, cursors and
                               # the daemon; no desktop needed

MIT License.
