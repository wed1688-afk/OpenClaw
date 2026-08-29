# tests for the 不動產廣告 PO 文刊登資訊 builder
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.property_ad import BROKER, Person, build_ad_footer, get_salesperson


def test_footer_for_registered_salesperson():
    assert build_ad_footer("吳文欽") == (
        "九如處\n"
        "營業員：吳文欽　證號：(105)登字第311470號\n"
        "高雄7+1工商特許加盟店\n"
        "七加一不動產仲介經紀有限公司\n"
        "經紀人：郭錫斌　證號：(102)高市字第00908號"
    )


def test_broker_can_post_as_salesperson():
    assert build_ad_footer("郭錫斌") == (
        "九如處\n"
        "營業員：郭錫斌　證號：(102)高市字第00908號\n"
        "高雄7+1工商特許加盟店\n"
        "七加一不動產仲介經紀有限公司\n"
        "經紀人：郭錫斌　證號：(102)高市字第00908號"
    )


def test_accepts_unregistered_person():
    footer = build_ad_footer(Person("測試員", "(110)登字第00000號"))
    assert "營業員：測試員　證號：(110)登字第00000號" in footer
    assert footer.splitlines()[-1] == BROKER.line("經紀人")


def test_unknown_salesperson_raises():
    try:
        get_salesperson("查無此人")
    except KeyError as exc:
        assert "查無此人" in str(exc)
    else:
        raise AssertionError("expected KeyError for an unregistered salesperson")


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"{name} ok")
