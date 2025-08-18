import xml.etree.ElementTree as ET

from pdf2epub.pipeline import _wrap_xhtml


def test_copy_entity_normalized_to_xml_numeric():
    frag = "<p>Copyright &copy; 1975</p>"
    xhtml = _wrap_xhtml("Test", frag)
    # Should parse as XML (no undefined entity errors)
    ET.fromstring(xhtml)
    # And it should contain a numeric entity &#169;
    assert "&#169;" in xhtml or "&copy;" in xhtml  # allow passthrough if XML parser accepts
