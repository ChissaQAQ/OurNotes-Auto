"""切换账号：用标题菜单、用户中心、登录记录的 OCR 夹具模拟退出登录、选账号、登录。"""

import math

import pytest
from test_navigator import fake_clock, load_items, make_nav

from ournotes_auto.nav import navigator
from ournotes_auto.nav.account import match_rows
from ournotes_auto.nav.screens import in_roi, sdk_point, sdk_rect
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import NavigationError

A = "user_1234567890"  # 夹具里当前登录的账号（展开的列表里第二行）
B = "user_98765432100"  # 最近登录过的另一个账号（第一行）
SDK_FRAMES = ("user_center", "login_history", "login_history_expanded")
SDK_POPUP = (413, 118, 454, 484)  # 1280x720 时 SDK 弹窗的范围
# SDK 弹窗相对 1280x720 时的缩放比：1440p 下约 0.75（#54），DPI 调高时会更大
SDK_SCALES = [1.0, 0.745, 1.2]


class AccountGame:
    """截图就是画面名；登录记录里选中的账号、登录后的欢迎提示按 ``selected`` 改写，展开的列表按 ``rows`` 改写；
    SDK 弹窗按 ``sdk`` 以画面中心缩放（分辨率、DPI 和 1280x720 时不同）。

    ``ready_at``：账号自动登录好的时刻（假时钟），之前标题菜单里的用户中心是灰的、点不开（#97）。默认按打开菜单时的
    状态、菜单开着一直不变，``live`` 时随时变；``toast`` 时登录好后的 3 秒里标题画面上有「欢迎回来」；
    ``popup_s``：点了用户中心多久后弹窗才出来。"""

    size = (1920, 1080)

    def __init__(
        self,
        state: str,
        selected: str = A,
        rows: tuple[str, ...] = (B, A),
        welcome: str | None = None,
        sdk: float = 1.0,
        ready_at: float | None = None,
        live: bool = False,
        toast: bool = False,
        popup_s: float = 0.0,
    ):
        self.state = state
        self.selected = selected
        self.rows = rows
        self.welcome = welcome  # 欢迎提示里显示的账号（默认是选中的）
        self.sdk = sdk
        self.ready_at = ready_at
        self.live = live
        self.toast = toast
        self.popup_s = popup_s
        self.clock = [0.0]  # make() 换成假时钟
        self.center_ok = True  # 这次打开的菜单里用户中心能不能点
        self.popup_at: float | None = None  # 用户中心弹窗出来的时刻
        self.menu_opens: list[float] = []  # 打开标题菜单的时刻
        self.closes: list[float] = []  # 点标题菜单「关闭」的时刻
        self.bad_closes = 0  # 用户中心弹窗正要出来时点了关闭
        self.taps: list[tuple[str, tuple[int, int]]] = []
        self.drags = 0
        self.drag_from: list[tuple[float, float]] = []
        self.after_login = "login_welcome"

    def logged_in(self) -> bool:
        return self.ready_at is None or self.clock[0] >= self.ready_at

    def grab(self):
        if self.popup_at is not None and self.clock[0] >= self.popup_at:
            self.state, self.popup_at = "user_center", None
        return self.state, 0.0

    def read(self, frame, roi=None):
        items = load_items(frame)
        if frame == "title" and self.toast and self.logged_in() and self.clock[0] - self.ready_at < 3:
            items = [*items, OcrItem(520, 30, 240, 30, f"{self.selected},欢迎回来")]
        if frame in ("login_history", "login_welcome"):
            name = self.welcome or self.selected if frame == "login_welcome" else self.selected
            items = [OcrItem(it.x, it.y, it.w, it.h, it.text.replace(A, name)) for it in items]
        elif frame == "login_history_expanded":
            names = iter(self.rows)
            out = []
            for it in items:
                if it.text in (A, B):
                    name = next(names, None)
                    if name is None:
                        continue
                    it = OcrItem(it.x, it.y, it.w, it.h, name)
                out.append(it)
            items = out
        if frame in SDK_FRAMES:
            items = [
                OcrItem(*sdk_rect((it.x, it.y, it.w, it.h), self.sdk), it.text) if in_roi(it, SDK_POPUP) else it
                for it in items
            ]
        return items

    def _row_at(self, p):
        for it in self.read("login_history_expanded"):
            if it.text in self.rows and math.dist(p, (it.x + it.w / 2, it.cy)) < 30 * self.sdk:
                return it.text
        return None

    def tap(self, x, y):
        p = (x * 1280 / self.size[0], y * 720 / self.size[1])
        self.taps.append((self.state, p))

        def near(target):
            if self.state in SDK_FRAMES:  # 要按缩放比换算后点准（不换算时 1.2 倍下也差不到 30px）
                return math.dist(p, sdk_point(target, self.sdk)) < 10
            return math.dist(p, target) < 30

        if self.state == "title" and near((1232, 46)):
            self.state = "title_menu"
            self.center_ok = self.logged_in()
            self.menu_opens.append(self.clock[0])
        elif self.state == "title_menu" and near((502, 298)):
            if (self.logged_in() if self.live else self.center_ok) and self.popup_at is None:
                if self.popup_s:
                    self.popup_at = self.clock[0] + self.popup_s
                else:
                    self.state = "user_center"
        elif self.state == "title_menu" and near((640, 573)):
            self.closes.append(self.clock[0])
            self.bad_closes += self.popup_at is not None
            self.state = "title"
        elif self.state == "user_center" and near((639, 524)):
            self.state = "login_history"
        elif self.state == "login_history" and near((798, 305)):
            self.state = "login_history_expanded"
        elif self.state == "login_history" and near((640, 497)):
            self.state = self.after_login
        elif self.state == "login_history_expanded" and (name := self._row_at(p)):
            self.selected = name
            self.state = "login_history"

    # 拖动账号列表：画面不变（只有这几个账号）
    def down(self, finger, x, y):
        self.drags += 1
        self.drag_from.append((x * 1280 / self.size[0], y * 720 / self.size[1]))

    def move(self, *a):
        pass

    def up(self, *a):
        pass

    def flush(self):
        pass


def make(game, monkeypatch):
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    monkeypatch.setattr("ournotes_auto.nav.song_select.time.sleep", lambda s: None)
    game.clock = fake_clock(monkeypatch, nav)
    return nav


def never_dangerous(game):
    """展开的列表里每行右边的 ⓧ（删除登录记录）在 x≈802（1280x720 时），从来不点。"""
    assert not [p for s, p in game.taps if s == "login_history_expanded" and p[0] > sdk_point((760, 0), game.sdk)[0]]


@pytest.mark.parametrize("sdk", SDK_SCALES)
def test_switch_to_other_account(monkeypatch, sdk):
    """标题 → 菜单 → 用户中心 → 退出登录 → 登录记录（选中的是 A）→ 展开 → 选 B → 登录 → 欢迎提示。"""
    game = AccountGame("title", sdk=sdk)
    nav = make(game, monkeypatch)
    nav.switch_account("98765")
    assert game.selected == B and game.state == "login_welcome"
    assert [s for s, _ in game.taps] == [
        "title",
        "title_menu",
        "user_center",
        "login_history",
        "login_history_expanded",
        "login_history",
    ]
    never_dangerous(game)


def test_switch_to_selected_account(monkeypatch):
    """登录记录里选中的就是要的账号（同一个账号再登录一次）：直接点登录，不展开列表。"""
    game = AccountGame("title")
    nav = make(game, monkeypatch)
    nav.switch_account(A.upper())  # 不分大小写
    assert game.selected == A and game.state == "login_welcome"
    assert "login_history_expanded" not in [s for s, _ in game.taps]


def test_switch_from_game_restarts(monkeypatch):
    """已经在游戏里：重启游戏回到标题画面再切换。"""
    game = AccountGame("home")
    nav = make(game, monkeypatch)
    restarts = []

    def restart():
        restarts.append(game.state)
        game.state = "title"

    nav.restart_app = restart
    nav.switch_account(B)
    assert restarts == ["home"] and game.selected == B


def test_switch_from_game_without_restart_fails(monkeypatch):
    game = AccountGame("home")
    nav = make(game, monkeypatch)
    with pytest.raises(NavigationError, match="没能回到标题画面"):
        nav.switch_account(B)
    assert game.taps == []


@pytest.mark.parametrize("toast", [False, True])
def test_user_center_disabled_until_logged_in(monkeypatch, toast):
    """#97：账号还没自动登录好就打开了菜单，用户中心是灰的、菜单开着一直点不开：点几秒还在菜单上就关掉菜单，
    等一会儿（看到「欢迎回来」就不等）再打开。"""
    game = AccountGame("title", ready_at=1012.0, toast=toast)
    nav = make(game, monkeypatch)
    nav.switch_account(B)
    assert game.selected == B and game.state == "login_welcome"
    first, again = game.menu_opens
    (closed,) = game.closes
    assert closed - first < 10 and game.bad_closes == 0
    if toast:
        assert 1012 <= again < 1015
    else:
        assert again - closed >= 8


def test_user_center_never_enabled_fails(monkeypatch):
    """用户中心一直点不开：和原来一样到时间报错，其间反复关掉菜单再打开。"""
    game = AccountGame("title", ready_at=math.inf)
    nav = make(game, monkeypatch)
    with pytest.raises(NavigationError, match="180s 内没能切换到账号"):
        nav.switch_account(B)
    assert len(game.closes) > 5 and game.bad_closes == 0
    assert "user_center" not in [s for s, _ in game.taps]


@pytest.mark.parametrize("popup_s", [0.0, 1.0, 2.5])
@pytest.mark.parametrize("ready", [3.0, 5.5, 6.0, 7.0])
def test_user_center_slow_popup_not_closed(monkeypatch, ready, popup_s):
    """用户中心快到时限才变成能点、弹窗又出来得慢：弹窗正要出来时不点关闭（关闭的位置在弹窗里）。"""
    game = AccountGame("title", ready_at=1000.0 + ready, live=True, popup_s=popup_s)
    nav = make(game, monkeypatch)
    nav.switch_account(B)
    assert game.selected == B and game.bad_closes == 0
    assert len(game.closes) == (ready > 6.0)


@pytest.mark.parametrize("sdk", SDK_SCALES)
def test_unknown_account_fails(monkeypatch, sdk):
    """登录记录里没有：拖一下列表，内容没变就报错，不点任何一行、不点登录。"""
    game = AccountGame("title", sdk=sdk)
    nav = make(game, monkeypatch)
    with pytest.raises(NavigationError, match="登录记录里没有账号 nobody"):
        nav.switch_account("nobody")
    assert game.drags == 1 and game.selected == A
    assert math.dist(game.drag_from[0], sdk_point((600, 520), sdk)) < 2  # 在账号名那一列，离 ⓧ 远
    login = sdk_point((640, 497), sdk)
    assert not [p for s, p in game.taps if s == "login_history" and math.dist(p, login) < 30 * sdk]
    never_dangerous(game)


def test_ambiguous_account_fails(monkeypatch):
    """好几个账号都对得上：报错，不随便选一个。"""
    game = AccountGame("title", selected="someone")
    nav = make(game, monkeypatch)
    with pytest.raises(NavigationError, match="有 2 个账号名包含 user_"):
        nav.switch_account("user_")
    assert game.selected == "someone"


def test_welcome_for_wrong_account_fails(monkeypatch):
    """登录后欢迎提示里的账号不对：报错。"""
    game = AccountGame("login_history", selected=B, welcome=A)
    nav = make(game, monkeypatch)
    with pytest.raises(NavigationError, match="登录的账号不对"):
        nav.switch_account("98765")


def test_empty_account_rejected(monkeypatch):
    nav = make(AccountGame("title"), monkeypatch)
    with pytest.raises(ValueError):
        nav.switch_account("  ")


@pytest.mark.parametrize("sdk", SDK_SCALES)
def test_logged_out_logs_in_last_account(monkeypatch, sdk):
    """不是在切换账号时碰到登录记录（账号被退出了）：登录上次登录的账号，接着进游戏。"""
    game = AccountGame("login_history", sdk=sdk)
    game.after_login = "home"
    nav = make(game, monkeypatch)
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert game.selected == A
    assert [s for s, _ in game.taps] == ["login_history"]


def test_login_history_1440p(monkeypatch):
    """#54 的截图（2560x1440，SDK 弹窗约 0.75 倍）：「登录」「∨」点在截图上量的位置。"""
    game = AccountGame("login_history_1440p")
    game.size = (2560, 1440)
    nav = make(game, monkeypatch)
    items = load_items("login_history_1440p")
    assert nav._pick_account(items, A, None)
    assert not nav._pick_account(items, B, None)
    (_, login), (_, expand) = game.taps
    assert math.dist(login, (639, 461)) < 5
    assert math.dist(expand, (759, 317)) < 5


def test_match_rows():
    rows = [OcrItem(0, 0, 10, 10, A), OcrItem(0, 0, 10, 10, B)]
    assert match_rows(rows, "１２３４") == rows[:1]  # 全角数字也认
    assert match_rows(rows, "user") == rows
