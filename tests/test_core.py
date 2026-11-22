"""Automated regression tests for course-archiver core functions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.downloader import (
    _format_chapter_dir,
    _safe_name,
    build_lecture_dir,
    build_out_path,
    download_pdf,
    is_downloaded,
)
from src.scraper import Course, get_course_tree


class TestDownloaderCore(unittest.TestCase):
    def test_safe_name_reserved_devices(self):
        self.assertEqual(_safe_name("CON"), "_CON")
        self.assertEqual(_safe_name("con.mp4"), "_con.mp4")
        self.assertEqual(_safe_name("aux.pdf"), "_aux.pdf")
        self.assertEqual(_safe_name("NUL"), "_NUL")
        self.assertEqual(_safe_name("COM1.txt"), "_COM1.txt")
        self.assertEqual(_safe_name("lpt9.pdf"), "_lpt9.pdf")
        self.assertEqual(_safe_name("com3.bin"), "_com3.bin")
        self.assertEqual(_safe_name("01_CON.mp4"), "01_CON.mp4")

    def test_safe_name_trailing_characters(self):
        self.assertEqual(_safe_name("Lecture 1. "), "Lecture 1")
        self.assertEqual(_safe_name("Title..."), "Title")
        self.assertEqual(_safe_name("   "), "untitled")
        self.assertEqual(_safe_name("..."), "untitled")
        self.assertEqual(_safe_name(".mp4"), "untitled.mp4")

    def test_safe_name_control_characters(self):
        # Newline, carriage return, and tab are illegal on Windows filesystem
        self.assertEqual(_safe_name("Lecture 1\nIntro.pdf"), "Lecture 1_Intro.pdf")
        self.assertEqual(_safe_name("Title\r\nSubtitle"), "Title__Subtitle")
        self.assertEqual(_safe_name("Chapter\t01"), "Chapter_01")

    def test_safe_name_extension_preservation(self):
        long_title = "01_" + "A" * 90 + ".mp4"
        res = _safe_name(long_title, max_len=80)
        self.assertTrue(res.endswith(".mp4"))
        self.assertLessEqual(len(res), 80)
        self.assertEqual(res, "01_" + "A" * 73 + ".mp4")

    def test_chapter_dir_tagging(self):
        self.assertEqual(_format_chapter_dir("Chapter 01", "Physics LIVE"), "Chapter 01_LIVE")
        self.assertEqual(_format_chapter_dir("Chapter 01_LIVE", "Physics LIVE"), "Chapter 01_LIVE")
        self.assertEqual(_format_chapter_dir("Chapter 01 (Live)", "Physics LIVE"), "Chapter 01 (Live)")
        self.assertEqual(_format_chapter_dir("Chapter 01 Delivery", "Physics LIVE"), "Chapter 01 Delivery_LIVE")
        self.assertEqual(_format_chapter_dir("Chapter 01", "Chemistry ARCHIVE"), "Chapter 01_ARCHIVE")
        self.assertEqual(_format_chapter_dir("Chapter 01_ARCHIVE", "Chemistry ARCHIVE"), "Chapter 01_ARCHIVE")
        self.assertEqual(_format_chapter_dir("Chapter 02", "English : Paper 1"), "Chapter 02_Paper 1")
        self.assertEqual(_format_chapter_dir("Chapter 02_Paper 1", "English : Paper 1"), "Chapter 02_Paper 1")
        self.assertEqual(_format_chapter_dir("Chapter 03", "Regular Subject"), "Chapter 03")

    def test_build_lecture_dir_and_out_path(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            out_p = build_out_path(
                base,
                "Course: Alpha",
                "Physics LIVE",
                "Chapter 01",
                1,
                "Intro\nLecture",
                "01_Intro.mp4",
            )
            # Ensure no control chars or colons
            self.assertNotIn("\n", str(out_p))
            self.assertNotIn(":", out_p.parts[-1])
            self.assertTrue(out_p.name.endswith(".mp4"))

    def test_is_downloaded(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            pdf = base / "Lecture.pdf"
            url = base / "Lecture.url"
            mp4 = base / "Video.mp4"

            self.assertFalse(is_downloaded(pdf))
            self.assertFalse(is_downloaded(mp4))

            # Test direct file existence
            mp4.write_text("dummy video", encoding="utf-8")
            self.assertTrue(is_downloaded(mp4))

            # Test .url shortcut existence
            url.write_text("[InternetShortcut]\nURL=https://google.com\n", encoding="utf-8")
            self.assertTrue(is_downloaded(pdf))

    def test_mongo_null_sorting_and_field_defaults(self):
        # 1. Null position sorting
        data = [{"position": None}, {"position": 3}, {"position": 1}, {"position": 0}]
        sorted_data = sorted(data, key=lambda x: int(x.get("position") or 0))
        self.assertEqual([d["position"] for d in sorted_data], [None, 0, 1, 3])

        # 2. Null videoNumber sorting
        v_data = [{"videoNumber": None}, {"videoNumber": 5}, {"videoNumber": 2}]
        sorted_v = sorted(v_data, key=lambda x: int(x.get("videoNumber") or 0))
        self.assertEqual([d["videoNumber"] for d in sorted_v], [None, 2, 5])

        # 3. get_course_tree with null fields (videoType: None, bunnyVideoId: None, videoTitle: None)
        course = Course(id="c1", name="Test Course")
        with patch("src.scraper._api") as mock_api:
            mock_api.side_effect = [
                {"subjects": [{"_id": "s1", "subjectName": "Physics"}]},
                {"chapters": [{"_id": "ch1", "chapterName": "Ch1", "position": None}]},
                {"videos": [{
                    "_id": "v1",
                    "videoTitle": None,
                    "videoNumber": None,
                    "videoType": None,
                    "bunnyVideoId": None,
                }]},
            ]
            with patch("src.scraper.get_session_info", return_value=("u1", "tok")):
                get_course_tree(course, {})
                self.assertEqual(len(course.subjects), 1)
                self.assertEqual(len(course.subjects[0].chapters), 1)
                ch = course.subjects[0].chapters[0]
                self.assertEqual(ch.position, 0)
                self.assertEqual(len(ch.videos), 1)
                v = ch.videos[0]
                self.assertEqual(v.title, "")
                self.assertEqual(v.number, 0)
                self.assertEqual(v.video_type, "bunny")
                self.assertEqual(v.bunny_id, "")

    def test_magic_bytes_detection(self):
        valid_pdf = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n"
        bom_pdf = b"\xef\xbb\xbf%PDF-1.4\n"
        html_page = b"<!DOCTYPE html><html><head><title>Access Denied</title></head></html>"
        self.assertTrue(valid_pdf.startswith(b"%PDF") or b"%PDF" in valid_pdf[:1024])
        self.assertTrue(bom_pdf.startswith(b"%PDF") or b"%PDF" in bom_pdf[:1024])
        self.assertFalse(html_page.startswith(b"%PDF") or b"%PDF" in html_page[:1024])

    def test_download_pdf_permission_wall_fallback(self):
        """HTTP 403 / 401 login walls fall back to .url shortcut cleanly without raising."""
        with tempfile.TemporaryDirectory() as td:
            out_p = Path(td) / "Lecture.pdf"
            with patch("requests.Session.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.status_code = 403
                mock_resp.headers = {"Content-Type": "text/html"}
                mock_get.return_value = mock_resp

                download_pdf("https://example.com/restricted.pdf", out_p, {})
                self.assertTrue(out_p.with_suffix(".url").exists())
                self.assertFalse(out_p.exists())
                content = out_p.with_suffix(".url").read_text(encoding="utf-8")
                self.assertIn("URL=https://example.com/restricted.pdf", content)

    def test_download_pdf_google_drive_confirmation(self):
        """Google Drive large file virus-scan confirmation extracts token and downloads."""
        with tempfile.TemporaryDirectory() as td:
            out_p = Path(td) / "Lecture.pdf"
            with patch("requests.Session.get") as mock_get:
                # 1st call: returns HTML warning page with confirm token
                resp1 = MagicMock()
                resp1.status_code = 200
                resp1.headers = {"Content-Type": "text/html; charset=utf-8"}
                resp1.cookies = {}
                resp1.text = '<a href="/uc?export=download&confirm=t_token123&id=1Abc">Download</a>'

                # 2nd call: returns binary PDF stream
                resp2 = MagicMock()
                resp2.status_code = 200
                resp2.headers = {"Content-Type": "application/pdf"}
                resp2.iter_content.return_value = [b"%PDF-1.4 binary content"]

                mock_get.side_effect = [resp1, resp2]

                download_pdf("https://drive.google.com/file/d/1Abcdefghij1234567890/view", out_p, {})
                self.assertTrue(out_p.exists())
                self.assertEqual(out_p.read_bytes(), b"%PDF-1.4 binary content")

    def test_resolve_stream(self):
        from src.scraper import Video, resolve_stream

        # 1. YouTube direct
        yt_vid = Video(
            id="v1", title="YT Vid", number=1, video_type="youtube",
            bunny_id="", watch_url="https://redwansmethod.com/watch/v1",
            youtube_url="https://youtube.com/watch?v=123"
        )
        url, ref = resolve_stream(yt_vid, {}, "tok")
        self.assertEqual(url, "https://youtube.com/watch?v=123")
        self.assertEqual(ref, "https://www.youtube.com/")

        # 2. Bunny CDN with valid HEAD response (200 OK)
        bunny_vid = Video(
            id="v2", title="Bunny Vid", number=2, video_type="bunny",
            bunny_id="b-123", watch_url="https://redwansmethod.com/watch/v2",
            youtube_url="https://youtube.com/watch?v=fallback"
        )
        with patch("src.scraper._api", return_value={"url": "https://video.b-cdn.net/playlist.m3u8"}):
            with patch("src.scraper._session.head") as mock_head:
                mock_head.return_value = MagicMock(status_code=200)
                url, ref = resolve_stream(bunny_vid, {}, "tok")
                self.assertEqual(url, "https://video.b-cdn.net/playlist.m3u8")
                self.assertEqual(ref, "https://www.redwansmethod.com/")

        # 3. Bunny CDN with 404 HEAD response -> falls back to YouTube
        with patch("src.scraper._api", return_value={"url": "https://video.b-cdn.net/playlist.m3u8"}):
            with patch("src.scraper._session.head") as mock_head:
                mock_head.return_value = MagicMock(status_code=404)
                url, ref = resolve_stream(bunny_vid, {}, "tok")
                self.assertEqual(url, "https://youtube.com/watch?v=fallback")
                self.assertEqual(ref, "https://www.youtube.com/")


if __name__ == "__main__":
    unittest.main()
