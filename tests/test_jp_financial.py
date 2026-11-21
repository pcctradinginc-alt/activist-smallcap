from activist.study.dataset import is_jp_financial


def test_banks_and_insurers_excluded():
    for n in ["株式会社三菱ＵＦＪフィナンシャル・グループ", "野村證券株式会社", "日本生命保険相互会社", "三井住友信託銀行株式会社",
              "Goldman Sachs Securities"]:
        assert is_jp_financial(n), n


def test_activists_not_excluded():
    for n in ["ストラテジックキャピタル", "Oasis Management Company Ltd.", "エフィッシモ キャピタル マネージメント",
              "3D Investment Partners Pte. Ltd.", "Dalton Investments"]:
        assert not is_jp_financial(n), n
