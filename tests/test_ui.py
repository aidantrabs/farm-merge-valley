from fmvbot import ui
from fmvbot.tasks.rewards import free_timed


def btn(name="", text="", tex=()):
    return {"name": name, "text": text, "tex": list(tex), "x": 0, "y": 0, "w": 50, "h": 20}


def test_real_money_and_gem_buttons_are_never_positive():
    for b in [
        btn("purchaseIAP-gems_small", "$ 9.99", ["btn_default_green_idle"]),
        btn("purchaseInventory-crates_003", "20", ["btn_default_blue_idle", "icon_btn_gem"]),
        btn("purchasePremiumArea", "Purchase", ["btn_default_blue_idle"]),
        btn("orderFastForward", "15", ["icon_btn_orders_fastforward_gem"]),
    ]:
        assert ui.costs_something(b)
        assert not ui.is_positive(b)


def test_free_claims_are_positive():
    assert ui.is_positive(btn("orderClaim", "Claim", ["btn_default_green_idle"]))
    assert ui.is_positive(btn("collectButton", "Collect"))
    assert ui.is_free(btn("orderFastForward", "FREE"))
    assert not ui.is_free(btn("orderFastForward", "FREE 5"))


def test_account_and_social_buttons_are_avoided():
    assert not ui.is_positive(btn("collect rewards", "Sign in", ["btn_default_green_idle"]))
    assert not ui.is_positive(btn("", "Invite friends"))


def test_marketplace_freebies_skip_ads_unless_allowed():
    ad = btn("purchaseTime_crates_rv", "FREE", ["btn_default_blue_idle", "icon_btn_video_white"])
    plain = btn("purchaseTime_crates", "FREE", ["btn_default_blue_idle"])
    assert not free_timed(ad, ads=False)
    assert free_timed(ad, ads=True)
    assert free_timed(plain, ads=False)
    assert not free_timed(btn("purchaseInventory-crates_001", "200"), ads=True)


def test_close_buttons():
    assert ui.is_close(btn("close", "", ["btn_close_idle"]))
    assert not ui.is_close(btn("orderMake", "Make"))


def test_energy_costs_are_fine_but_premium_is_not():
    assert not ui.spends_premium(btn("remove", "Remove 5", ["btn_default_green_idle", "icon_energy"]))
    assert not ui.spends_premium(btn("requirements_[object Object]", "Open", ["btn_default_green_idle"]))
    assert ui.spends_premium(btn("remove", "Remove 30", ["btn_default_green_idle", "icon_btn_gem"]))
    assert ui.spends_premium({**btn("x", "Open 10"), "priced": True})
    assert ui.spends_premium(btn("", "Watch ad"))
    assert not ui.is_free(btn("orderFastForward", "FREE", ["icon_btn_video_white"]))


def test_land_buy_bubble_is_not_premium_but_popup_buy_is_a_cost():
    land = btn("purchaseArea", "Buy", ["btn_default_green_idle"])
    assert not ui.spends_premium(land)
    assert ui.costs_something(land)


def test_gem_price_only_for_plain_gem_costs():
    assert ui.gem_price(btn("orderFastForward", "15", ["icon_btn_orders_fastforward_gem"])) == 15
    assert ui.gem_price(btn("skip", "1,250", ["btn_default_green_idle", "icon_btn_gem"])) == 1250
    assert ui.gem_price(btn("skip", "1.2K", ["icon_btn_gem"])) == 1200
    assert ui.gem_price(btn("orderFastForward", "FREE")) is None
    assert ui.gem_price(btn("purchaseIAP-gems_small", "$ 9.99", ["icon_btn_gem"])) is None
    assert ui.gem_price(btn("orderFastForward", "15", ["icon_btn_video_white", "icon_btn_gem"])) is None
    assert ui.gem_price(btn("remove", "Remove 5", ["icon_energy"])) is None
    assert ui.gem_price(btn("purchaseInventory-crates_30", "20", ["btn_marketplace_green_idle", "icon_btn_gem"])) == 20
    assert ui.gem_price(btn("purchaseInventory-gems_3500", "CA$29.99 CA$39.99", ["icon_btn_gem"])) is None
    assert ui.gem_price(btn("purchaseInventory-gems_200", "2.99", ["icon_btn_gem"])) is None


def test_close_button_is_the_x_not_the_panel_holding_it():
    panel = {**btn("content", "Fast Forward Event", ["popup_background_event_9slice", "btn_close_big_idle"]), "w": 250}
    x = {**btn("close", "", ["btn_close_big_idle", "btn_close_big_down"]), "w": 21}
    assert ui.close_button([panel, x]) is x
