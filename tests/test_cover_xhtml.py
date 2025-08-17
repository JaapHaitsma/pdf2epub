from pdf2epub.pipeline import _build_cover_xhtml


def test_cover_xhtml_has_correct_namespaces_and_img():
    x = _build_cover_xhtml("images/cover.jpg")
    assert "<html xmlns='http://www.w3.org/1999/xhtml'" in x
    assert "xmlns:epub='http://www.idpf.org/2007/ops'" in x
    assert "<meta name='viewport' content='width=device-width, initial-scale=1'/>" in x
    assert "<img src='images/cover.jpg' alt='Cover'/>" in x
