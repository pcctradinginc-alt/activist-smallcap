

def test_subject_sic_from_header():
    from activist.parse.filing13d import subject_sic
    txt = ("SUBJECT COMPANY:\n COMPANY DATA:\n  COMPANY CONFORMED NAME: X INC\n"
           "  STANDARD INDUSTRIAL CLASSIFICATION: SERVICES-PREPACKAGED SOFTWARE [7372]\n"
           "FILED BY:\n COMPANY DATA:\n  STANDARD INDUSTRIAL CLASSIFICATION:  [6282]\n")
    assert subject_sic(txt) == "7372"
    assert subject_sic("no header") is None
