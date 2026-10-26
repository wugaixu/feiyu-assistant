import unittest
from app.send_receipt import decode,fields

def number(n):
    data=bytearray()
    while n>127:data.append((n&127)|128);n>>=7
    return bytes(data)+bytes([n])
def scalar(k,n):return number(k*8)+number(n)
def blob(k,b):return number(k*8+2)+number(len(b))+b
def receipt(send,outer=0):return scalar(3,outer)+blob(6,blob(100,send))

class ReceiptTests(unittest.TestCase):
    def test_http_success_alone_is_not_acceptance(self):
        self.assertFalse(decode(scalar(3,0))['accepted'])
    def test_server_id_and_zero_status_required(self):
        self.assertTrue(decode(receipt(scalar(1,123)))['accepted'])
        self.assertFalse(decode(receipt(scalar(1,123)+scalar(3,1)))['accepted'])
        self.assertFalse(decode(receipt(scalar(1,123),7))['accepted'])
    def test_business_rejection_despite_outer_success(self):
        value=decode(receipt(scalar(1,123)+scalar(5,2)+blob(6,b'{"status_code":7505}')))
        self.assertFalse(value['accepted'])
        value=decode(receipt(scalar(1,123)+blob(6,b'{"status_code":8101,"raw_check_code":1}')))
        self.assertFalse(value['accepted'])
        self.assertEqual(value['business_status'],8101)
    def test_truncated_payload_fails_closed(self):
        with self.assertRaises(ValueError):fields(b'\x32\x7fshort')
