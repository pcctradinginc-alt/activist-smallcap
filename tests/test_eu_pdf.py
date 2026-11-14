from pathlib import Path
from activist.sources import eu_pdf

FX = Path(__file__).parent / "fixtures" / "eu"


def test_tr1_text_filer_ratio_isin():
    d = eu_pdf.parse_tr1_text((FX / "oslo_flagging_pdf.txt").read_text())
    assert d["filer_name"] == "Mirabella Financial Services LLP"
    assert d["holding_ratio"] == 0.1283          # Gesamtanteil, nicht die Vormeldung (19,99 %)
    assert d["isin"] == "NO0013162693" and d["evidence"]


def test_amf_text_filer_ratio_direction():
    d = eu_pdf.parse_amf_text((FX / "amf_franchissement_pdf.txt").read_text())
    assert d["filer_name"].startswith("BlackRock Inc")
    assert d["holding_ratio"] == 0.0496 and d["direction"] == "down" and d["isin"] == "FR0000044448"


def test_parse_any_picks_language():
    assert eu_pdf.parse_any((FX / "amf_franchissement_pdf.txt").read_text())["holding_ratio"] == 0.0496
    assert eu_pdf.parse_any((FX / "oslo_flagging_pdf.txt").read_text())["filer_name"].startswith("Mirabella")


def test_enrich_amf_and_oslo_with_monkeypatch(monkeypatch):
    amf_txt = (FX / "amf_franchissement_pdf.txt").read_text()
    oslo_txt = (FX / "oslo_flagging_pdf.txt").read_text()
    monkeypatch.setattr(eu_pdf, "download", lambda url: b"")
    monkeypatch.setattr(eu_pdf, "extract_text", lambda b: amf_txt)
    ev = eu_pdf.enrich_amf({"event_id": "AMF_BDIF:1", "url": "http://x/a.pdf", "filer_name": None})
    assert ev["pdf_enriched"] and ev["filer_name"].startswith("BlackRock") and ev["issuer_isin"] == "FR0000044448" and ev["pdf_evidence"]

    class R:
        def json(self):
            return {"data": {"message": {"attachments": [{"id": 9, "name": "x.xlsx"}, {"id": 7, "name": "form.pdf"}]}}}
    monkeypatch.setattr(eu_pdf, "request", lambda *a, **k: R())
    monkeypatch.setattr(eu_pdf, "extract_text", lambda b: oslo_txt)
    ev = eu_pdf.enrich_oslo({"event_id": "OSLO_NEWSWEB:686499"})
    assert ev["pdf_enriched"] and ev["holding_ratio"] == 0.1283


def test_enrich_failure_leaves_event(monkeypatch):
    monkeypatch.setattr(eu_pdf, "download", lambda url: (_ for _ in ()).throw(RuntimeError("boom")))
    ev = eu_pdf.enrich_amf({"event_id": "A:1", "url": "u"})
    assert ev["pdf_enriched"] is False and "filer_name" not in ev


def test_scanned_pdf_without_text_is_unenriched(monkeypatch):
    monkeypatch.setattr(eu_pdf, "download", lambda url: b"")
    monkeypatch.setattr(eu_pdf, "extract_text", lambda b: "")
    assert eu_pdf.enrich_amf({"event_id": "A:1", "url": "u"})["pdf_enriched"] is False
