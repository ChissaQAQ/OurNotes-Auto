"""切换账号：用标题菜单、用户中心、登录记录的 OCR 夹具模拟退出登录、选账号、登录。"""

import math

import pytest
from test_navigator import fake_clock, load_items, make_nav

from ournotes_auto.nav import navigator
from ournotes_auto.nav.account import match_rows
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import NavigationError

A = "user_1234567890"  # 夹具里当前登录的账号（展开的列表里第二行）
B = "user_98765432100"  # 最近登录过的另一个账号（第一行）


class AccountGame:
    """截图就是画面名；登录记录里选中的账号、登录后的欢迎提示按 ``selected`` 改写，展开的列表按 ``rows`` 改写。"""

    size = (1920, 1080)

    def __init__(self, state: str, selected: str = A, rows: tuple[str, ...] = (B, A), welcome: str | None = None):
        self.state = state
        self.selected = selected
        self.rows = rows
        self.welcome = welcome  # 欢迎提示里显示的账号（默认是选中的）
        self.taps: list[tuple[str, tuple[int, int]]] = []
        self.drags = 0
        self.after_login = "login_welcome"

    def grab(self):
        return self.state, 0.0

    def read(self, frame, roi=None):
        items = load_items(frame)
        if frame in ("login_history", "login_welcome"):
            name = self.welcome or self.selected if frame == "login_welcome" else self.selected
            return [OcrItem(it.x, it.y, it.w, it.h, it.text.replace(A, name)) for it in items]
        if frame == "login_history_expanded":
            names = iter(self.rows)
            out = []
            for it in items:
                if it.text in (A, B):
                    name = next(names, None)
                    if name is None:
                        continue
                    it = OcrItem(it.x, it.y, it.w, it.h, name)
                out.append(it)
            return out
        return items

    def _row_at(self, p):
        for it in self.read("login_history_expanded"):
            if it.text in self.rows and math.dist(p, (it.x + it.w / 2, it.cy)) < 30:
                return it.text
        return None

    def tap(self, x, y):
        p = (x * 1280 / self.size[0], y * 720 / self.size[1])
        self.taps.append((self.state, p))

        def near(target):
            return math.dist(p, target) < 30

        if self.state == "title" and near((1232, 46)):
            self.state = "title_menu"
        elif self.state == "title_menu" and near((502, 298)):
            self.state = "user_center"
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
    def down(self, *a):
        self.drags += 1

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
    fake_clock(monkeypatch, nav)
    return nav


def never_dangerous(game):
    """展开的列表里每行右边的 ⓧ（删除登录记录）在 x≈802，从来不点。"""
    assert not [p for s, p in game.taps if s == "login_history_expanded" and p[0] > 760]


def test_switch_to_other_account(monkeypatch):
    """标题 → 菜单 → 用户中心 → 退出登录 → 登录记录（选中的是 A）→ 展开 → 选 B → 登录 → 欢迎提示。"""
    game = AccountGame("title")
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


def test_unknown_account_fails(monkeypatch):
    """登录记录里没有：拖一下列表，内容没变就报错，不点任何一行、不点登录。"""
    game = AccountGame("title")
    nav = make(game, monkeypatch)
    with pytest.raises(NavigationError, match="登录记录里没有账号 nobody"):
        nav.switch_account("nobody")
    assert game.drags == 1 and game.selected == A
    assert not [p for s, p in game.taps if s == "login_history" and math.dist(p, (640, 497)) < 30]
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


def test_logged_out_logs_in_last_account(monkeypatch):
    """不是在切换账号时碰到登录记录（账号被退出了）：登录上次登录的账号，接着进游戏。"""
    game = AccountGame("login_history")
    game.after_login = "home"
    nav = make(game, monkeypatch)
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert game.selected == A
    assert [s for s, _ in game.taps] == ["login_history"]


def test_match_rows():
    rows = [OcrItem(0, 0, 10, 10, A), OcrItem(0, 0, 10, 10, B)]
    assert match_rows(rows, "１２３４") == rows[:1]  # 全角数字也认
    assert match_rows(rows, "user") == rows
