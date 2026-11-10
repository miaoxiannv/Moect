"""Preset text-face regressions with synthetic messages and no network."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.data import Archive
from core.exporting import export_session
from core.inline_faces import ArchiveFaces, face_catalog
from core.account_exporting import write_collection
from tests.test_core import SESSION, fixture
from tests.test_resources import ASSETS

PANEL_ORDER = '''微笑 撇嘴 色 发呆 得意 流泪 害羞 闭嘴 睡 大哭
尴尬 发怒 调皮 呲牙 惊讶 难过 囧 抓狂 吐 偷笑
愉快 白眼 傲慢 困 惊恐 憨笑 悠闲 咒骂 疑问 嘘
晕 衰 骷髅 敲打 再见 擦汗 抠鼻 鼓掌 坏笑 右哼哼
鄙视 委屈 快哭了 阴险 亲亲 可怜 笑脸 生病 脸红 破涕为笑
恐惧 失望 无语 嘿哈 捂脸 奸笑 机智 皱眉 耶 吃瓜
加油 汗 天啊 Emm 社会社会 旺柴 好的 打脸 哇 翻白眼
666 让我看看 叹气 苦涩 裂开 嘴唇 爱心 心碎 拥抱 强
弱 握手 胜利 抱拳 勾引 拳头 OK 合十 啤酒 咖啡
蛋糕 玫瑰 凋谢 菜刀 炸弹 便便 月亮 太阳 庆祝 礼物
红包 發 福 烟花 爆竹 猪头 跳跳 发抖 转圈'''.split()


def registry(folder):
    script = (folder / 'builtin-faces.js').read_text(encoding='utf-8')
    return json.loads(script.split('=', 1)[1].rstrip(';\n'))


class InlineFaceTests(unittest.TestCase):
    def test_all_109_panel_faces_have_distinct_valid_preset_files(self):
        catalog = face_catalog(str(ASSETS.resolve()))
        names = ['[' + name + ']' for name in PANEL_ORDER]
        self.assertEqual(len(names), 109)
        self.assertEqual(list(catalog)[:109], names)
        self.assertEqual(len({catalog[name]['file'] for name in names}), 109)
        for name, entry in catalog.items():
            with self.subTest(face=name):
                blob = (ASSETS / 'wechat-faces' / entry['file']).read_bytes()
                self.assertTrue(blob.startswith(b'\x89PNG\r\n\x1a\n'))
                self.assertEqual(hashlib.sha256(blob).hexdigest(), entry['sha256'])
        self.assertNotIn('[图片]', catalog)
        self.assertNotIn('[未定义]', catalog)

    def test_nested_faces_share_assets_without_rewriting_text_or_urls(self):
        message = {'text': '[抠鼻][抠鼻]文字[未定义] https://example.com/[微笑]',
                   'quote': {'text': '[捂脸]'},
                   'forward': {'messages': [{'text': '[囧]'}, {'text': '[庆祝]'}]}}
        original = copy.deepcopy(message)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            faces = ArchiveFaces(ASSETS)
            faces.remember(message)
            faces.write(output)
            mapping = registry(output)
            self.assertEqual(set(mapping), {'[抠鼻]', '[捂脸]', '[囧]', '[庆祝]'})
            self.assertEqual(len(list((output / 'builtin-faces').glob('*.png'))), 4)
            self.assertTrue(all((output / value).is_file() for value in mapping.values()))
        self.assertEqual(message, original)

    def test_missing_or_corrupt_preset_falls_back_without_failing_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / 'assets';assets.mkdir()
            (assets / 'wechat-faces').mkdir()
            output = root / 'out';output.mkdir()
            catalog = face_catalog(str(ASSETS.resolve()))
            small = {name: catalog[name] for name in ('[抠鼻]', '[捂脸]')}
            (assets / 'wechat-faces.json').write_text(json.dumps(small), encoding='utf-8')
            (assets / 'wechat-faces' / small['[抠鼻]']['file']).write_bytes(b'not an image')
            faces = ArchiveFaces(assets)
            faces.remember({'text': '[抠鼻][捂脸]'})
            faces.write(output)
            self.assertEqual(registry(output), {})

    def test_html_forward_pages_share_faces_and_json_txt_keep_original_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = Archive(fixture(root / 'db', count=1))
            try:
                message = next(archive.messages(SESSION))
                message['text'] = '[抠鼻][捂脸]普通文字[未定义]'
                child = dict(message, text='[囧][庆祝]', server_id='900')
                message['forward'] = {'title': '合成转发', 'messages': [child], 'total': 1}
                output = root / 'out';output.mkdir()
                for fmt in ('html', 'json', 'txt'):
                    with patch.object(archive, 'messages', return_value=iter([copy.deepcopy(message)])), \
                            patch('core.resources.ResourceStore.enrich', side_effect=lambda row: row):
                        result = export_session(archive, archive.sessions()[0], {'format': fmt}, output,
                                                ASSETS, lambda _: None, lambda: None)
                    path = Path(result['path'])
                    if fmt == 'html':
                        self.assertEqual(set(registry(path.parent)), {'[抠鼻]', '[捂脸]', '[囧]', '[庆祝]'})
                        self.assertIn('builtin-faces.js', path.read_text(encoding='utf-8'))
                        forward = list(path.parent.glob('forward_*.html'))
                        self.assertEqual(len(forward), 1)
                        self.assertIn('builtin-faces.js', forward[0].read_text(encoding='utf-8'))
                        chunk = (path.parent / 'c000001.js').read_text(encoding='utf-8')
                        self.assertIn(message['text'], chunk)
                    else:
                        self.assertIn(message['text'], path.read_text(encoding='utf-8'))
            finally:
                archive.close()

    def test_cancel_is_respected_before_preset_assets_are_written(self):
        faces = ArchiveFaces(ASSETS)
        faces.remember({'text': '[捂脸]'})

        def cancel():
            raise RuntimeError('cancelled')

        faces.cancel = cancel
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(RuntimeError, 'cancelled'):
            faces.write(Path(temporary))

    def test_moments_collect_body_and_comment_faces_but_preserve_json_and_urls(self):
        data = {'category': 'moments', 'items': [
            {'text': '[抠鼻] https://example.com/[微笑]',
             'comments': [{'text': '[捂脸][囧]'}, {'text': '[未知]'}]}]}
        original = copy.deepcopy(data['items'])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_collection(output, ASSETS, data)
            self.assertEqual(set(registry(output)), {'[抠鼻]', '[捂脸]', '[囧]'})
            self.assertEqual(json.loads((output / 'data.json').read_text(encoding='utf-8'))['items'], original)
            html = (output / 'index.html').read_text(encoding='utf-8')
            self.assertNotIn('__INLINE_FACES__', html)
            self.assertIn('window.MoectFaces', html)
            write_collection(output, ASSETS, {'category': 'stickers', 'items': []})
            self.assertEqual(registry(output), {})
