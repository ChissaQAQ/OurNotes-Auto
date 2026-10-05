"""切换账号：在标题画面退出当前的 B 站账号，从登录记录里选另一个账号登录（不用输密码）。

画面顺序（坐标均为 1280x720 设计尺寸）::

    标题画面 -[右上角 ☰]-> 标题菜单 -[用户中心]-> 用户中心（B 站 SDK）-[退出登录]->（没有确认，约 5 秒后）
    登录记录（显示上次登录的账号）-[右边的 ∨]-> 账号列表（按最近登录排列）-[那个账号]-> 登录记录（选中了它）
    -[登录]-> 顶部提示「<账号>,欢迎回来」，回到标题画面（之后照常 TAP TO START 进游戏）

- 只认登录记录里已经有的账号：没登录过的账号要玩家自己先在游戏里登录一次（密码、验证码都由玩家输入）。
- 用户中心往下滚有「注销」（删除账号），账号列表每行右边的 ⓧ 会删掉这条登录记录，都绝不能点：
  只按 OCR 认出的「退出登录」、账号名本身的位置点。
- 已经进到游戏里时重启游戏回到标题画面（游戏里没有回标题画面的按钮）。
"""

from __future__ import annotations

import logging
import time

from ..result_reader import OcrItem
from .screens import Screen, account_key, center, find, login_expanded, login_rows, title_startable

logger = logging.getLogger(__name__)

BTN_TITLE_MENU = (1232, 46)  # 标题画面右上角 ☰
BTN_MENU_USER_CENTER = (502, 298)
BTN_MENU_CLOSE = (640, 573)
BTN_USER_CENTER_BACK = (452, 168)  # 用户中心左上角 <
BTN_LOGIN = (640, 497)  # 登录记录：登录
BTN_LOGIN_EXPAND = (798, 305)  # 登录记录：选中账号右边的 ∨（展开账号列表）
# 展开的账号列表认不全时往上拖（从账号名那一列拖，离每行右边的 ⓧ 远一点）
LIST_DRAG = ((600, 520), (600, 340))
MAX_LIST_DRAGS = 5
LOGOUT_WAIT_S = 20.0  # 退出登录后登录记录多久没出来就报错
LOGIN_RETAP_S = 5.0  # 点了登录多久还在登录记录上就再点一次
ACCOUNT_SCREENS = frozenset((Screen.TITLE, Screen.TITLE_MENU, Screen.USER_CENTER, Screen.LOGIN_HISTORY))


def match_rows(rows: list[OcrItem], name: str) -> list[OcrItem]:
    """账号名包含 ``name`` 的行。"""
    key = account_key(name)
    return [it for it in rows if key in account_key(it.text)]


class AccountMixin:
    def switch_account(self, name: str, timeout_s: float = 180.0) -> None:
        """退出当前账号，在登录记录里选账号名包含 ``name`` 的账号登录，回到标题画面就返回（没点 TAP TO START）。"""
        from .navigator import IN_GAME_SCREENS

        if not account_key(name):
            raise ValueError("账号名不能为空")
        deadline = time.monotonic() + timeout_s
        logout_at: float | None = None  # 点退出登录的时刻
        login_at: float | None = None  # 点登录的时刻
        restarted = False
        drags = 0
        seen_rows: tuple[str, ...] | None = None
        while True:
            if time.monotonic() > deadline:
                raise self._fail(f"{timeout_s:.0f}s 内没能切换到账号 {name}")
            screen, items = self.look()
            now = time.monotonic()
            if login_at is not None:
                if welcome := find(items, "欢迎回来"):
                    if not match_rows([welcome], name):
                        raise self._fail(f"登录的账号不对：{welcome.text}")
                    logger.info("已切换到账号 %s", name)
                    return
                if screen is Screen.TITLE and title_startable(items):
                    logger.info("已切换到账号 %s", name)  # 没赶上顶部的提示
                    return
            if screen is Screen.TITLE:
                if logout_at is not None:
                    if login_at is None and now - logout_at > LOGOUT_WAIT_S:
                        raise self._fail(f"退出登录后 {LOGOUT_WAIT_S:.0f}s 没出现登录记录")
                elif title_startable(items):
                    self.tap(BTN_TITLE_MENU, "标题菜单")
                    self._sleep(self.settle_s)
                    continue
            elif screen is Screen.TITLE_MENU:
                if logout_at is None:
                    self.tap(BTN_MENU_USER_CENTER, "用户中心")
                else:
                    self.tap(BTN_MENU_CLOSE, "关闭")
                self._sleep(self.settle_s)
                continue
            elif screen is Screen.USER_CENTER:
                if logout_at is None and (it := find(items, "退出登录", exact=True)):
                    logger.info("退出当前账号")
                    self.tap(center(it), "退出登录")
                    logout_at = now
                else:
                    self.tap(BTN_USER_CENTER_BACK, "返回")
                self._sleep(self.settle_s)
                continue
            elif screen is Screen.LOGIN_HISTORY:
                logout_at = logout_at or now
                if self._pick_account(items, name, login_at):
                    login_at = now
                elif login_expanded(items):
                    rows = tuple(it.text for it in login_rows(items))
                    if rows and not match_rows(login_rows(items), name):  # 没有行时列表还在展开
                        if rows == seen_rows or drags >= MAX_LIST_DRAGS:
                            raise self._fail(f"登录记录里没有账号 {name}（请先在游戏里手动登录一次这个账号）")
                        seen_rows = rows
                        drags += 1
                        self._drag(*LIST_DRAG)
                self._sleep(self.settle_s)
                continue
            elif screen in IN_GAME_SCREENS and logout_at is None:
                if restarted or self.restart_app is None:
                    raise self._fail("没能回到标题画面")
                logger.info("重启游戏回到标题画面")
                self.restart_app()
                restarted = True
                self._sleep(3.0)  # 别把重启前的画面当成还在游戏里
                continue
            elif screen not in ACCOUNT_SCREENS and self._common_step(screen, items):
                self._sleep(self.settle_s)
                continue
            self._sleep(1.0)

    def _pick_account(self, items: list[OcrItem], name: str, login_at: float | None) -> bool:
        """登录记录：选中的就是要的账号时点登录（返回 True），否则展开列表、点那个账号。"""
        rows = login_rows(items)
        if not login_expanded(items):
            if rows and match_rows(rows[:1], name):
                if login_at is not None and time.monotonic() - login_at < LOGIN_RETAP_S:
                    return False
                self.tap(BTN_LOGIN, "登录")
                return True
            self.tap(BTN_LOGIN_EXPAND, "展开登录记录")
            return False
        matches = match_rows(rows, name)
        if len(matches) > 1:
            raise self._fail(f"登录记录里有 {len(matches)} 个账号名包含 {name}，请写得更完整些")
        if matches:
            self.tap(center(matches[0]), "选择账号")
        return False
