"""图片通道的边界检查，网络均模拟，不消耗付费额度。"""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import tactics_board as t

PNG=b'\x89PNG\r\n\x1a\n'+b'fixture'


class TacticsImageTests(unittest.TestCase):
    def test_reject_arbitrary_address_or_path(self):
        for kind,pid in [('player','../1'),('https://evil.test','1'),('player','https://evil.test'),('player','1&x=2'),('player','1/2'),('team','')]:
            with self.assertRaises(ValueError):t.load_photo(kind,pid)

    def test_only_raster_images(self):
        self.assertEqual(t.raster_type(PNG),'image/png')
        self.assertEqual(t.raster_type(b'\xff\xd8\xffdata'),'image/jpeg')
        with self.assertRaises(ValueError):t.raster_type(b'<svg onload="alert(1)">')
        with self.assertRaises(ValueError):t.raster_type(b'<html>Access denied</html>')

    def test_cached_image_does_not_call_network(self):
        with tempfile.TemporaryDirectory() as root,patch.object(t,'CACHE_DIR',Path(root)),patch.object(t,'build_opener') as net:
            (Path(root)/'player_123.bin').write_bytes(PNG)
            raw,mime=t.load_photo('player','123')
            self.assertEqual(raw,PNG);self.assertEqual(mime,'image/png');net.assert_not_called()

    def test_download_fixed_host_and_cache(self):
        response=io.BytesIO(PNG);opener=Mock();opener.open.return_value=response
        with tempfile.TemporaryDirectory() as root,patch.object(t,'CACHE_DIR',Path(root)),patch.object(t,'build_opener',return_value=opener):
            raw,mime=t.load_photo('team','9906')
            request=opener.open.call_args[0][0]
            self.assertEqual(request.full_url,'https://images.fotmob.com/image_resources/logo/teamlogo/9906.png')
            self.assertEqual((Path(root)/'team_9906.bin').read_bytes(),PNG)

    def test_reject_oversized_or_non_image_response(self):
        for raw in [b'x'*(t.LIMIT+1),b'<svg/>']:
            opener=Mock();opener.open.return_value=io.BytesIO(raw)
            with tempfile.TemporaryDirectory() as root,patch.object(t,'CACHE_DIR',Path(root)),patch.object(t,'build_opener',return_value=opener):
                with self.assertRaises(ValueError):t.load_photo('player','123')

    def test_external_redirect_rejected(self):
        with self.assertRaises(ValueError):t.ImageRedirect().redirect_request(None,None,302,'',{},'https://other.test/a.png')


if __name__=='__main__':unittest.main()
