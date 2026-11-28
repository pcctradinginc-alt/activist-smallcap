import io
import zipfile
from pathlib import Path
import pytest
from activist.sources import edinet_docs as ed

HDR = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]


def make_zip(items, extra_noise=True):
    lines = ["\t".join(f'"{h}"' for h in HDR)]
    if extra_noise:
        lines.append('\t'.join(f'"{x}"' for x in ["jpdei_cor:EDINETCodeDEI", "EDINETコード", "FilingDateInstant", "提出日時点", "その他", "時点", "－", "－", "E12345"]))
    for elem, label, ctx, val in items:
        lines.append("\t".join(f'"{x}"' for x in [elem, label, ctx, "当期", "個別", "時点", "－", "－", val]))
    raw = ("\r\n".join(lines) + "\r\n").encode("utf-16")  # mit BOM wie bei EDINET
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("XBRL_TO_CSV/jplvh010000-lvh-001_E12345-000_2024-01-01_01_2024-01-05.csv", raw)
        z.writestr("XBRL_TO_CSV/readme.txt", b"ignored")
    return buf.getvalue()


def base(sec, ratio, purpose, name="テスト株式会社", ratio_label="株券等保有割合"):
    return [
        ("jplvh_cor:SecurityCodeOfIssuer", "発行者の証券コード", "FilingDateInstant", sec),
        ("jplvh_cor:NameOfIssuer", "発行者の名称", "FilingDateInstant", name),
        ("jplvh_cor:PurposeOfHolding", "保有目的", "FilingDateInstant", purpose),
        ("jplvh_cor:HoldingRatioOfShareCertificatesEtc", ratio_label, "FilingDateInstant", ratio),
        ("jplvh_cor:HoldingRatioOfShareCertificatesEtcPerLastReport", "直前の報告書に記載された株券等保有割合", "FilingDateInstant", "0.0510"),
        ("jplvh_cor:TotalNumberOfStocksEtcIssued", "発行済株式等総数", "FilingDateInstant", "12,345,678"),
        ("jpdei_cor:FilerNameInJapaneseDEI", "提出者名", "FilingDateInstant", "アクティビスト・ファンド"),
    ]


def test_important_proposal_positive_percent_5digit():
    z = make_zip(base("72030", "7.21", "発行会社の経営陣に対し、企業価値向上のため重要提案行為等を行うことがある。"))
    r = ed.parse_large_holding_zip(z)
    assert r["issuer_sec_code"] == "7203" and r["issuer_name"] == "テスト株式会社"
    assert r["important_proposal"] is True and "重要提案行為" in r["important_proposal_evidence"]
    assert r["holding_ratio"] == pytest.approx(0.0721)
    assert r["previous_holding_ratio"] == pytest.approx(0.051)
    assert r["total_shares_outstanding"] == 12345678.0
    assert r["filer_name"] == "アクティビスト・ファンド"


def test_important_proposal_negated_fraction():
    p = "純投資。ただし、重要提案行為等を行うことを目的としていない。"
    r = ed.parse_large_holding_zip(make_zip(base("130A0", "0.0721", p)))
    assert r["important_proposal"] is False and r["important_proposal_evidence"]
    assert r["issuer_sec_code"] == "130A"
    assert r["holding_ratio"] == pytest.approx(0.0721)


def test_pure_investment_no_mention():
    r = ed.parse_large_holding_zip(make_zip(base("9984", "5.5％", "純投資")))
    assert r["important_proposal"] is False and r["important_proposal_evidence"] is None
    assert r["issuer_sec_code"] == "9984" and r["holding_ratio"] == pytest.approx(0.055)


@pytest.mark.parametrize("txt", ["重要提案行為等を行う予定はない。", "重要提案行為等を行わない。", "重要提案行為等を行うことを目的としていない"])
def test_negations(txt):
    assert ed.important_proposal(txt)[0] is False


def test_label_fallback_without_known_elements():
    rows = [
        {"要素ID": "x:A", "項目名": "発行者の証券コード", "コンテキストID": "c", "値": "６７５８０"},
        {"要素ID": "x:B", "項目名": "保有目的", "コンテキストID": "c", "値": "重要提案行為等を行う。"},
        {"要素ID": "x:C", "項目名": "株券等保有割合", "コンテキストID": "c", "値": "10.5"},
    ]
    r = ed.parse_rows(rows)
    assert r["issuer_sec_code"] == "6758" and r["important_proposal"] and r["holding_ratio"] == pytest.approx(0.105)


def test_joint_holders_total_else_max():
    el = "jplvh_cor:HoldingRatioOfShareCertificatesEtc"
    rows = [{"要素ID": el, "項目名": "株券等保有割合", "コンテキストID": c, "値": v}
            for c, v in [("FilingDateInstant_Holder1Member", "0.03"), ("FilingDateInstant_Holder2Member", "0.04"),
                         ("FilingDateInstant_TotalMember", "0.07")]]
    assert ed.parse_rows(rows)["holding_ratio"] == pytest.approx(0.07)
    assert ed.parse_rows(rows[:2])["holding_ratio"] == pytest.approx(0.04)


def test_normalize_sec_code():
    n = ed.normalize_sec_code
    assert n("72030") == "7203" and n("130A0") == "130A" and n("7203") == "7203" and n("") is None and n("abc") is None


def test_empty_zip():
    r = ed.parse_large_holding_zip(make_zip([], extra_noise=False))
    assert r["issuer_sec_code"] is None and r["holding_ratio"] is None and r["important_proposal"] is False


def test_code_map_fixture():
    m = ed.edinet_code_map(Path(__file__).parent / "fixtures/edinet/EdinetcodeDlInfo_trim.csv")
    assert m["E00004"]["name"] == "カネコ種苗株式会社" and m["E00004"]["sec_code"] == "1376"
    assert m["E02144"]["sec_code"] == "7203"


class _Resp:
    def __init__(self, ctype, content=b"", status=200, js=None):
        self.headers = {"content-type": ctype}; self.content = content; self.status_code = status; self._js = js; self.text = ""
    def json(self): return self._js
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError("http")


def test_fetch_csv(monkeypatch):
    calls = {}
    def fake(url, params=None, timeout=None):
        calls["url"], calls["params"] = url, params
        return _Resp("application/octet-stream", b"PKzip")
    monkeypatch.setattr(ed.requests, "get", fake)
    assert ed.fetch_csv("S100ABCD", "SECRET") == b"PKzip"
    assert calls["url"].endswith("/documents/S100ABCD") and calls["params"]["type"] == 5


def test_fetch_csv_json_error_hides_key(monkeypatch):
    monkeypatch.setattr(ed.requests, "get", lambda *a, **k: _Resp("application/json; charset=utf-8", js={"statusCode": 404, "message": "SECRET not found"}))
    with pytest.raises(RuntimeError) as e:
        ed.fetch_csv("S1", "SECRET")
    assert "SECRET" not in str(e.value) and "404" in str(e.value)


def test_issuer_role_conflict_and_filer_code_exclusion():
    items = base("72030", "7.21", "純投資")
    items.insert(0, ("x:SecurityCodeOfFiler", "提出者の証券コード", "c", "83060"))
    result = ed.parse_large_holding_zip(make_zip(items))
    assert result["issuer_sec_code"] == "7203" and result["issuer_role_explicit"]
    items.append(("x:IssuerSecurityCode", "発行者の証券コード", "c", "83060"))
    result = ed.parse_large_holding_zip(make_zip(items))
    assert result["issuer_sec_code"] is None and result["issuer_role_conflict"]


def test_generic_security_code_is_not_issuer_evidence():
    result = ed.parse_large_holding_zip(make_zip([("x:SecurityCode", "証券コード", "c", "83060")]))
    assert result["issuer_sec_code"] is None and not result["issuer_role_explicit"]


def test_concrete_interventions_require_more_than_conditional_or_alliance_language():
    from activist.sources.edinet_docs import proposal_category

    assert proposal_category("提出者は、発行者に対して増配を要求することがある。重要提案行為等。") == "generic_or_negated_proposal"
    assert proposal_category("資本業務提携契約に基づき取締役候補者の指名を提案する。") == "strategic_alliance"
    assert proposal_category("発行者に対して事業の一部の譲渡を提案している。") == "specific_intervention"
    assert proposal_category("提出者は発行者の非公開化を目的に公開買付を行う予定です。") == "control_transaction"
