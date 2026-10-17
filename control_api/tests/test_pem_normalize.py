"""A private key pasted into a one-line dashboard field loses its line breaks; the loader must still accept it."""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from control_api import runner as rn


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def _loads(text):
    return serialization.load_pem_private_key(text.encode(), password=None)


def test_intact_pem_unchanged():
    pem = _pem()
    assert rn.normalize_pem(pem) == pem


def test_pem_with_spaces_instead_of_newlines_loads():
    pem = _pem()
    flat = " ".join(pem.split())
    assert _loads(rn.normalize_pem(flat)).key_size == 2048


def test_pem_with_literal_backslash_n_loads():
    pem = _pem()
    assert _loads(rn.normalize_pem(pem.replace("\n", "\n"))).key_size == 2048


def test_pem_with_no_separators_at_all_loads():
    pem = _pem()
    body = "".join(pem.split("\n")[1:-2])
    assert _loads(rn.normalize_pem(f"-----BEGIN PRIVATE KEY-----{body}-----END PRIVATE KEY-----")).key_size == 2048


def test_non_pem_and_empty_pass_through():
    assert rn.normalize_pem(None) is None and rn.normalize_pem("") == ""
    assert rn.normalize_pem("not a key") == "not a key"


def _body(pem: str) -> list:
    return pem.strip().splitlines()[1:-1]


def test_bare_base64_body_without_begin_end_loads():
    pem = _pem()
    assert _loads(rn.normalize_pem("".join(_body(pem)))).key_size == 2048
    assert _loads(rn.normalize_pem(" ".join(_body(pem)))).key_size == 2048


def test_bare_rsa_pkcs1_body_loads():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()
    assert _loads(rn.normalize_pem("".join(_body(pem)))).key_size == 2048
