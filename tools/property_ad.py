# 不動產廣告 PO 文刊登資訊
"""Build the disclosure block that must appear on real-estate ad posts.

依《不動產經紀業管理條例》第 22 條，廣告及銷售說明書應載明經紀業名稱、
經紀人姓名及其證照字號。本模組依「九如處」的固定範例格式產生該區塊：

    九如處
    營業員：吳文欽　證號：(105)登字第311470號
    高雄7+1工商特許加盟店
    七加一不動產仲介經紀有限公司
    經紀人：郭錫斌　證號：(102)高市字第00908號
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

# 姓名與證號之間使用全形空格，與刊登範例一致
FULL_WIDTH_SPACE = "　"

OFFICE_NAME = "九如處"
STORE_NAME = "高雄7+1工商特許加盟店"
COMPANY_NAME = "七加一不動產仲介經紀有限公司"


@dataclass(frozen=True)
class Person:
    """A licensed 營業員 (salesperson) or 經紀人 (broker)."""

    name: str
    license_no: str

    def line(self, title: str) -> str:
        """Render one titled line, e.g. 營業員：吳文欽　證號：(105)登字第311470號"""
        return f"{title}：{self.name}{FULL_WIDTH_SPACE}證號：{self.license_no}"


# 九如處的簽章經紀人
BROKER = Person("郭錫斌", "(102)高市字第00908號")

# 已登錄的營業員；經紀人本人刊登時亦以營業員身分具名
SALESPERSONS = {
    "郭錫斌": BROKER,
    "吳文欽": Person("吳文欽", "(105)登字第311470號"),
}


def get_salesperson(name: str) -> Person:
    """Look up a registered 營業員 by name."""
    try:
        return SALESPERSONS[name]
    except KeyError:
        known = "、".join(SALESPERSONS)
        raise KeyError(f"查無營業員「{name}」，已登錄者：{known}") from None


def build_ad_footer(
    salesperson: Person | str,
    broker: Person = BROKER,
    office: str = OFFICE_NAME,
    store: str = STORE_NAME,
    company: str = COMPANY_NAME,
) -> str:
    """Return the five-line disclosure block for an ad post.

    `salesperson` accepts a registered name or a `Person` for someone not
    (yet) in `SALESPERSONS`.
    """
    if isinstance(salesperson, str):
        salesperson = get_salesperson(salesperson)

    return "\n".join(
        [
            office,
            salesperson.line("營業員"),
            store,
            company,
            broker.line("經紀人"),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="產生不動產廣告 PO 文刊登資訊")
    parser.add_argument("name", nargs="?", default=BROKER.name, help="營業員姓名")
    parser.add_argument("--license", dest="license_no", help="證號（未登錄的營業員需自行提供）")
    args = parser.parse_args()

    person: Person | str
    person = Person(args.name, args.license_no) if args.license_no else args.name
    print(build_ad_footer(person))


if __name__ == "__main__":
    main()
