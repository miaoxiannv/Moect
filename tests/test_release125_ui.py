"""Built 1.2.5 interface and offline media controls with entirely synthetic data."""
import base64
import functools
import http.server
import json
import io
import math
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from PIL import Image

from core.data import Archive
from core.exporting import export_session
from core.account_exporting import write_collection
from core.resources import ResourceStore
from tests.test_core import fixture, SESSION
from tests.test_resources import PNG, ASSETS
from tests.test_inline_faces import PANEL_ORDER

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Release125UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True);cls.thread.start()
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def app(self, invalid_timestamp=False, viewport=None, device_scale_factor=1):
        context = self.browser.new_context(viewport=viewport or {'width':1080, 'height':800},
                                           device_scale_factor=device_scale_factor, reduced_motion='reduce')
        self.addCleanup(context.close)
        page = context.new_page()
        if invalid_timestamp:
            page.add_init_script('window.__invalidTimestamp=true')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        image = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
        page.add_init_script('window.__image=' + json.dumps(image) + r''';
          window.__calls=[];window.__payloads=[];window.__assetPayloads=[];window.__prefs={zip:false,encrypt:false,has_password:false};
          const online={configured:false,checking:false,initial_check_done:true,revision:0,error:'',has_update:false,unread_count:0,announcements:[],download:{status:'idle'}};
          const state={version:'1.2.5',theme:'light',accent:'violet',appearance:{},accents:[],export_dir:'Synthetic exports',
            storage_dir:'D:/Synthetic cache',storage_busy:false,storage_warning:'',tasks:[],online,
            session_sort:{field:'lastActivity',order:'desc'},library:{name:'Synthetic',profile:{name:'Synthetic',wxid:'synthetic'},live:true,demo:false}};
          const sessions=[{username:'friend',display:'普通好友',category:'friend'},{username:'gh_sample',display:'示例公众号',category:'official'},
            {username:'filehelper',display:'文件传输助手',category:'other'},{username:'sample@chatroom',display:'合成群',category:'group',is_group:true}]
            .map(row=>({...row,last_time:1800000000,avatar_url:'',summary:''}));
          if(window.__invalidTimestamp)sessions[0].last_time=1e300;
          window.pywebview={api:{invoke:async(action,payload={})=>{
            window.__calls.push(action);let result={ok:true};
            if(action==='state')result=state;
            else if(action==='detect')result={running:false,accounts:[{id:'synthetic-account',name:'Synthetic',wxid:'synthetic',connected:true}]};
            else if(action==='sessions')result=sessions;
            else if(action==='settings'){Object.assign(state,payload);result=state;}
            else if(action==='tutorial_status')result={seen:true};
            else if(action==='startup_reconnect')result={skipped:true};
            else if(action==='archive_preferences'){
              if(window.__failPreferences && Object.keys(payload).length)throw new Error('Synthetic write failure');
              for(const key of ['zip','encrypt'])if(typeof payload[key]==='boolean')window.__prefs[key]=payload[key];
              if(payload.password)window.__prefs.has_password=true;
              if(window.__prefs.encrypt)window.__prefs.zip=true;
              result=window.__prefs;
            }else if(action==='export'){window.__payloads.push(payload);result={job:'synthetic-export'};}
            else if(action==='account_assets')result={available:true,total:2,count:2,groups:[{id:'favorites',name:'合成表情',count:2}],posts:[],selection:{label:'我的朋友圈'}};
            else if(action==='export_account'){window.__assetPayloads.push(payload);result={job:'synthetic-assets'};}
            else if(action==='storage_info')result={path:state.storage_dir,bytes:2048,roots:[{path:state.storage_dir,current:true,bytes:2048,categories:{archives:1024,previews:1024}}]};
            else if(action==='storage_cleanup'||action==='choose_cache')result={job:'synthetic-storage'};
            else if(action==='choose_diagnostics')result={path:'Synthetic report.json'};
            else if(action==='qce_preview')result={messages:[{msgId:'0_1_1',msgSeq:1,msgTime:1800000000,senderUid:'friend',senderUin:'',senderAvatar:'',
              richContent:{media:{session:'friend',shard:0,local_id:1,server_id:'1',kind:'image'}},elements:[{textElement:{content:'[图片]'}}],sendType:0,
              peerUin:'friend',chatType:1,subMsgType:0,sendNickName:'普通好友'}],totalCount:1,currentPage:1,totalPages:1,hasNext:false};
            else if(action==='preview_media')result={kind:'image',url:window.__image,notice:''};
            else if(action==='online_status'||action==='online_check')result=online;
            else if(action.startsWith('window_'))result={maximized:false};
            return {result:structuredClone(result)};
          }}};
        ''')
        page.goto(f'http://127.0.0.1:{self.server.server_port}/ui-qce/index.html')
        page.wait_for_function('() => window.__ready===true && window.__wce.sessions.length===4')
        return page, errors

    def test_reply_preview_renders_body_once_and_keeps_distinct_titles_and_quoted_text(self):
        page, errors = self.app()
        page.evaluate('''() => {
          const invoke=window.pywebview.api.invoke;
          window.pywebview.api.invoke=async(action,payload)=>{
            const response=await invoke(action,payload);
            if(action==='qce_preview'){
              const template=response.result.messages[0];
              const rows=[
                {text:'独立回复正文',title:'独立回复正文',reply:{sender:'引用者',text:'被引用的原文'}},
                {text:'链接说明',title:'独立链接标题'},
                {text:'原样重复\\n原样重复'},
                {text:'相同文字',title:'相同文字',reply:{sender:'引用者',text:'相同文字'}},
                {text:'https://example.com/quoted',title:'https://example.com/quoted',reply:{sender:'引用者',text:'网址原文'}},
              ];
              response.result.messages=rows.map(({text,...richContent},index)=>({...template,
                msgId:String(index),richContent,elements:[{textElement:{content:text}}]}));
              response.result.totalCount=rows.length;
            }
            return response;
          };
        }''')
        page.evaluate("window.__wce.showPreview('friend')")
        page.get_by_text('被引用的原文',exact=False).wait_for()
        self.assertEqual(page.get_by_text('独立回复正文',exact=True).count(),1)
        self.assertEqual(page.get_by_text('引用者：被引用的原文',exact=True).count(),1)
        self.assertEqual(page.get_by_text('链接说明',exact=True).count(),1)
        self.assertEqual(page.get_by_text('独立链接标题',exact=True).count(),1)
        self.assertEqual(page.get_by_text('原样重复\n原样重复',exact=True).count(),1)
        self.assertEqual(page.get_by_text('相同文字',exact=True).count(),1)
        self.assertEqual(page.get_by_text('引用者：相同文字',exact=True).count(),1)
        self.assertEqual(page.get_by_role('link',name='https://example.com/quoted',exact=True).count(),1)
        self.assertEqual(errors,[])

    def test_theme_icon_loops_on_hover_and_keyboard_focus_and_respects_reduced_motion(self):
        page, errors = self.app()
        page.emulate_media(reduced_motion='no-preference')
        button=page.get_by_title('切换主题',exact=True)
        box=button.bounding_box()
        self.assertEqual(page.evaluate('document.querySelector(".theme-icon-loop").getBoundingClientRect().width'),16)
        self.assertEqual(page.locator('.theme-toggle-icon').get_attribute('aria-hidden'),'true')
        button.hover()
        page.wait_for_function('() => getComputedStyle(document.querySelector(".theme-icon-loop")).opacity==="1"')
        names=page.evaluate('document.getAnimations().map(animation=>animation.animationName)')
        self.assertIn('moect-sun-turn',names)
        self.assertEqual(page.evaluate('window.__calls.filter(action=>action==="settings").length'),0)
        page.mouse.move(400,0)
        page.wait_for_function('() => getComputedStyle(document.querySelector(".theme-icon-static")).opacity==="1"')
        self.assertNotIn('moect-sun-turn',page.evaluate('document.getAnimations().map(animation=>animation.animationName)'))
        button.focus();page.keyboard.press('Tab');page.keyboard.press('Shift+Tab')
        page.wait_for_function('() => document.querySelector(".sidebar-theme-shortcut").matches(":focus-visible")')
        self.assertIn('moect-sun-turn',page.evaluate('document.getAnimations().map(animation=>animation.animationName)'))
        page.emulate_media(reduced_motion='reduce')
        button.click()
        page.wait_for_function('() => document.querySelector(".theme-toggle-icon").dataset.mode==="dark" && !document.querySelector(".sidebar-theme-shortcut").disabled')
        page.emulate_media(reduced_motion='no-preference')
        names=page.evaluate('document.getAnimations().map(animation=>animation.animationName)')
        self.assertIn('moect-moon-sway',names)
        self.assertEqual(names.count('moect-star-twinkle'),2)
        self.assertEqual(button.bounding_box(),box)
        page.emulate_media(reduced_motion='reduce')
        self.assertFalse(any(name and name.startswith('moect-') for name in page.evaluate('document.getAnimations().map(animation=>animation.animationName)')))
        self.assertEqual(errors,[])

    def test_sidebar_shortcuts_share_hover_style_equal_spacing_and_keep_online_actions(self):
        page, errors = self.app()
        selectors=['.online-update-shortcut','.online-notice-shortcut','.sidebar-theme-shortcut']
        for dark in (False,True):
            if dark:
                page.emulate_media(reduced_motion='reduce')
                page.get_by_title('切换主题',exact=True).click()
                page.wait_for_function('() => document.documentElement.classList.contains("dark") && !document.querySelector(".sidebar-theme-shortcut").disabled')
            boxes=[page.locator(selector).bounding_box() for selector in selectors]
            for box in boxes:
                self.assertEqual((box['width'],box['height']),(36,36))
                self.assertEqual(box['y'],boxes[0]['y'])
            self.assertAlmostEqual(boxes[1]['x']-boxes[0]['x'],boxes[2]['x']-boxes[1]['x'],delta=1)
            page.emulate_media(reduced_motion='no-preference')
            hover_styles=[]
            for selector, animation in zip(selectors,['moect-download-nudge','moect-announcement-sway','moect-moon-sway' if dark else 'moect-sun-turn']):
                page.locator(selector).hover()
                # Wait for the shared .2s surface transition, independent of the
                # continuously moving glyph inside each fixed-size button.
                page.wait_for_timeout(250)
                hover_styles.append(page.locator(selector).evaluate('element => {const style=getComputedStyle(element);return [style.backgroundColor,style.color,style.boxShadow,style.borderRadius]}'))
                self.assertIn(animation,page.evaluate('document.getAnimations().map(animation=>animation.animationName)'))
            self.assertEqual(hover_styles[0],hover_styles[1])
            self.assertEqual(hover_styles[1],hover_styles[2])
            page.emulate_media(reduced_motion='reduce')
            self.assertFalse(any(name and name.startswith('moect-') for name in page.evaluate('document.getAnimations().map(animation=>animation.animationName)')))
        page.get_by_role('button',name='检查更新',exact=True).click()
        page.get_by_role('dialog',name='软件更新',exact=True).wait_for()
        page.get_by_role('button',name='关闭',exact=True).click()
        page.get_by_role('dialog',name='软件更新',exact=True).wait_for(state='hidden')
        page.get_by_role('button',name='公告',exact=True).click()
        page.get_by_role('dialog',name='Moect 公告',exact=True).wait_for()
        self.assertEqual(errors,[])

    def test_theme_reveal_covers_both_directions_from_each_control_at_multiple_dpi(self):
        for viewport, dpi in [({'width':1062,'height':800},1), ({'width':1440,'height':900},1.5),
                              ({'width':1062,'height':800},2)]:
            with self.subTest(viewport=viewport,dpi=dpi):
                page, errors = self.app(viewport=viewport,device_scale_factor=dpi)
                page.emulate_media(reduced_motion='no-preference')
                page.evaluate('''() => {
                  const start=document.startViewTransition.bind(document);
                  window.__transitionCount=0;
                  document.startViewTransition=callback=>{
                    window.__transitionCount++;
                    const transition=start(callback);
                    transition.ready.then(()=>{
                      const animation=document.getAnimations().find(item=>item.animationName==='moect-theme-reveal');
                      animation.pause();animation.currentTime=540;window.__revealAnimation=animation;
                    },()=>{});
                    return transition;
                  };
                }''')
                for control, dark in [('切换主题',True), ('切换到浅色',False)]:
                    if not dark:
                        page.evaluate("window.__wce.setTab('settings')")
                        button=page.get_by_role('button',name=control,exact=True)
                    else:
                        button=page.get_by_title(control,exact=True)
                    # The settings button changes its accessible name on commit.
                    button=button.element_handle()
                    button.scroll_into_view_if_needed()
                    box=button.bounding_box()
                    old_color=page.evaluate('getComputedStyle(document.body).backgroundColor')
                    button.click()
                    page.wait_for_function('() => window.__revealAnimation?.playState==="paused" && document.documentElement.dataset.themeReveal==="active"')
                    details=page.evaluate('''() => {
                      const root=document.documentElement,style=getComputedStyle(root);
                      return {x:parseFloat(style.getPropertyValue('--theme-reveal-x')),
                        y:parseFloat(style.getPropertyValue('--theme-reveal-y')),
                        radius:parseFloat(style.getPropertyValue('--theme-reveal-radius')),
                        clip:getComputedStyle(root,'::view-transition-new(root)').clipPath,
                        newColor:getComputedStyle(document.body).backgroundColor,
                        dark:root.classList.contains('dark')};
                    }''')
                    self.assertAlmostEqual(details['x'],box['x']+box['width']/2,delta=1)
                    self.assertAlmostEqual(details['y'],box['y']+box['height']/2,delta=1)
                    self.assertEqual(details['dark'],dark)
                    self.assertIn('circle(',details['clip'])
                    corners=[(3,3),(3,viewport['height']-3),(viewport['width']-3,3),
                             (viewport['width']-3,viewport['height']-3)]
                    snapshot=Image.open(io.BytesIO(page.screenshot()))
                    old_rgb=tuple(map(int,re.findall(r'\d+',old_color)))
                    new_rgb=tuple(map(int,re.findall(r'\d+',details['newColor'])))
                    clip_radius=float(re.match(r'circle\(([\d.]+)',details['clip']).group(1))
                    self.assertAlmostEqual(clip_radius/details['radius'],.5,delta=.001)
                    easing=page.evaluate('''() => {
                      const animation=window.__revealAnimation,root=document.documentElement;
                      const radius=()=>parseFloat(getComputedStyle(root,'::view-transition-new(root)').clipPath.slice(7));
                      animation.currentTime=270;const quarter=radius();
                      animation.currentTime=810;const threeQuarter=radius();
                      animation.currentTime=540;
                      return {quarter,threeQuarter,duration:animation.effect.getTiming().duration};
                    }''')
                    self.assertEqual(easing['duration'],1080)
                    self.assertAlmostEqual(easing['quarter']/details['radius'],.0625,delta=.001)
                    self.assertAlmostEqual(easing['threeQuarter']/details['radius'],.9375,delta=.001)
                    inside_count=0
                    for x, y in corners:
                        inside=math.hypot(x-details['x'],y-details['y']) < clip_radius
                        inside_count+=inside
                        self.assertEqual(snapshot.getpixel((round(x*dpi),round(y*dpi)))[:3],new_rgb if inside else old_rgb)
                    self.assertGreater(inside_count,0)
                    self.assertLess(inside_count,4)
                    self.assertGreaterEqual(details['radius'],max(math.hypot(x-details['x'],y-details['y']) for x,y in corners))
                    self.assertTrue(button.is_disabled())
                    before=page.evaluate('window.__calls.filter(action=>action==="settings").length')
                    button.dispatch_event('click')
                    self.assertEqual(page.evaluate('window.__calls.filter(action=>action==="settings").length'),before)
                    page.evaluate('window.__revealAnimation.finish();window.__revealAnimation=null')
                    page.wait_for_function('() => !document.documentElement.dataset.themeReveal')
                    self.assertTrue(button.is_enabled())
                    self.assertEqual(page.evaluate('document.documentElement.style.getPropertyValue("--theme-reveal-radius")'),'')
                self.assertEqual(page.evaluate('window.__transitionCount'),2)
                self.assertEqual(errors,[])

    def test_theme_reveal_falls_back_and_recovers_from_capture_or_save_failure(self):
        for mode in ('reduced','unsupported','capture_failed','skipped','save_failed'):
            with self.subTest(mode=mode):
                page, errors = self.app()
                if mode!='reduced':
                    page.emulate_media(reduced_motion='no-preference')
                if mode=='unsupported':
                    page.evaluate('document.startViewTransition=undefined')
                elif mode=='capture_failed':
                    page.evaluate('() => {document.startViewTransition=()=>{throw new Error("Synthetic capture failure")}}')
                elif mode=='skipped':
                    page.evaluate('''() => {const start=document.startViewTransition.bind(document);
                      document.startViewTransition=callback=>{const transition=start(callback);transition.skipTransition();return transition};}''')
                elif mode=='save_failed':
                    page.evaluate('''() => {const invoke=window.pywebview.api.invoke;
                      window.pywebview.api.invoke=(action,payload)=>action==='settings'
                        ? Promise.reject(new Error('Synthetic settings failure')):invoke(action,payload);}''')
                button=page.get_by_title('切换主题',exact=True)
                button.click()
                page.wait_for_function('() => !document.querySelector(".sidebar-theme-shortcut").disabled')
                self.assertEqual(page.evaluate('document.documentElement.classList.contains("dark")'),mode!='save_failed')
                self.assertEqual(page.evaluate('document.documentElement.dataset.themeReveal'),None)
                self.assertEqual(errors,[])

    def test_theme_reveal_skips_on_resize_and_ignores_delayed_poll_appearance(self):
        page, errors = self.app()
        page.emulate_media(reduced_motion='no-preference')
        page.evaluate('''async() => {
          const invoke=window.pywebview.api.invoke,old=await invoke('state');
          const start=document.startViewTransition.bind(document);
          document.startViewTransition=callback=>{
            const transition=start(callback);
            transition.ready.then(()=>{
              const animation=document.getAnimations().find(item=>item.animationName==='moect-theme-reveal');
              animation.pause();window.__revealPaused=true;
            },()=>{});
            return transition;
          };
          window.__stalePolls=0;
          window.pywebview.api.invoke=(action,payload)=>{
            if(action==='state'){window.__stalePolls++;return Promise.resolve(structuredClone(old))}
            return invoke(action,payload);
          };
        }''')
        page.get_by_title('切换主题',exact=True).click()
        page.wait_for_function('() => window.__revealPaused===true')
        page.set_viewport_size({'width':1200,'height':850})
        page.wait_for_function('() => !document.documentElement.dataset.themeReveal')
        # The intercepted poll deliberately returns the pre-switch theme.
        page.wait_for_function('() => window.__stalePolls>0')
        self.assertEqual(page.evaluate('window.__wce.state.theme'),'dark')
        self.assertTrue(page.evaluate('document.documentElement.classList.contains("dark")'))
        self.assertTrue(page.get_by_title('切换主题',exact=True).is_enabled())
        self.assertEqual(errors,[])

    def test_delayed_theme_save_preserves_newer_task_state_and_defers_visual_commit(self):
        page, errors = self.app()
        page.evaluate('''() => {
          const invoke=window.pywebview.api.invoke;
          window.pywebview.api.invoke=async(action,payload)=>{
            const response=await invoke(action,payload);
            if(action==='settings'){
              await new Promise(resolve=>{window.__finishSettings=resolve});
            }else if(action==='state' && window.__finishSettings){
              response.result.library.name='Latest library';
              response.result.tasks=[{id:'latest-task',kind:'export',status:'queued',title:'Synthetic task'}];
            }
            return response;
          };
        }''')
        page.get_by_title('切换主题',exact=True).click()
        page.wait_for_function('() => window.__wce.state.tasks.some(task=>task.id==="latest-task")')
        self.assertEqual(page.evaluate('window.__wce.state.theme'),'light')
        self.assertFalse(page.evaluate('document.documentElement.classList.contains("dark")'))
        page.evaluate('window.__finishSettings()')
        page.wait_for_function('() => !document.querySelector(".sidebar-theme-shortcut").disabled')
        self.assertEqual(page.evaluate('window.__wce.state.theme'),'dark')
        self.assertEqual(page.evaluate('window.__wce.state.library.name'),'Latest library')
        self.assertEqual(page.evaluate('window.__wce.state.tasks[0].id'),'latest-task')
        self.assertEqual(errors,[])

    def test_invalid_timestamp_and_failed_preference_save_keep_interface_usable(self):
        page, errors = self.app(invalid_timestamp=True)
        page.evaluate("window.__wce.chooseItems(['friend'])")
        page.locator('summary').filter(has_text='更多设置').click()
        page.get_by_role('switch', name='使用密码加密压缩包', exact=True).click()
        page.get_by_label('压缩包密码', exact=True).fill('Synthetic unsaved password')
        page.evaluate('window.__failPreferences=true')
        page.keyboard.press('Escape')
        page.get_by_role('dialog', name='批量导出聊天记录', exact=True).wait_for(state='hidden')
        page.evaluate("window.__wce.setTab('stickers')")
        page.get_by_role('button', name='更多设置', exact=True).click()
        dialog = page.get_by_role('dialog', name='更多设置', exact=True)
        dialog.get_by_label('压缩包密码', exact=True).fill('Synthetic retry')
        page.keyboard.press('Escape')
        dialog.wait_for(state='hidden')
        self.assertEqual(errors, [])

    def test_settings_sections_are_separate_cards_in_light_and_dark_modes(self):
        page, errors = self.app()
        page.evaluate("window.__wce.setTab('settings')")
        cards = page.locator('.settings-page > section')
        self.assertEqual(cards.count(), 5)
        self.assertEqual(cards.locator(':scope > h3').all_text_contents(), ['导出位置','缓存与存储','数据来源','外观','使用帮助'])
        for dark in (False, True):
            if dark:
                page.get_by_role('button', name='切换到深色', exact=True).click()
                page.wait_for_function('() => document.documentElement.classList.contains("dark")')
            geometry = cards.evaluate_all('elements => elements.map(element => {const box=element.getBoundingClientRect(),style=getComputedStyle(element),heading=element.querySelector("h3");return {top:box.top,bottom:box.bottom,border:parseFloat(style.borderTopWidth),radius:parseFloat(style.borderRadius),separator:parseFloat(getComputedStyle(heading).borderBottomWidth)}})')
            for index, card in enumerate(geometry):
                self.assertGreaterEqual(card['border'], 1)
                self.assertGreaterEqual(card['radius'], 16)
                self.assertGreaterEqual(card['separator'], 1)
                if index:
                    self.assertGreaterEqual(card['top'] - geometry[index-1]['bottom'], 16)
        page.evaluate("window.__wce.setTab('stickers')")
        page.get_by_role('button', name='导出所选分类', exact=True).wait_for()
        self.assertEqual(page.get_by_text('把当前账号收藏的表情', exact=False).count(), 0)
        self.assertEqual(page.locator('p').filter(has_text='缩略图不会冒充原图').count(), 0)
        self.assertEqual(errors, [])

    def test_image_viewer_fits_shapes_preserves_mouse_anchor_and_supports_drag(self):
        context = self.browser.new_context(viewport={'width':1200, 'height':900})
        self.addCleanup(context.close)
        page = context.new_page();errors=[]
        page.on('pageerror', lambda error:errors.append(str(error)))
        links=[]
        for name, shape in [('portrait',(900,4000)),('landscape',(4000,900)),('small',(100,80))]:
            output=io.BytesIO();Image.new('RGB',shape,'#ab98da').save(output,format='PNG')
            uri='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
            links.append('<a class="photo-link" href="'+uri+'"><img alt="'+name+'" src="'+uri+'" width="60" height="60"></a>')
        page.set_content('<style>*{box-sizing:border-box}body{margin:0}</style><main>'+''.join(links)+'</main>')
        page.add_script_tag(content=(ROOT/'ui/archive-media.js').read_text(encoding='utf-8')+'\nMoectMedia.mount(document.querySelector("main"));')
        page.get_by_alt_text('portrait', exact=True).click()
        dialog=page.get_by_role('dialog',name='图片预览',exact=True)
        image=dialog.get_by_alt_text('预览图片',exact=True)
        image.wait_for(state='visible')
        def geometry():
            return page.evaluate('() => {const image=document.querySelector(".moect-image-frame img"),frame=image.parentElement,bounds=frame.getBoundingClientRect(),box=image.getBoundingClientRect(),matrix=new DOMMatrix(getComputedStyle(image).transform);return {frame:{x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height},image:{x:box.x,y:box.y,width:box.width,height:box.height},scale:matrix.a,x:matrix.e,y:matrix.f}}')
        def assert_fitted():
            state=geometry();frame,box=state['frame'],state['image']
            self.assertGreaterEqual(box['x'],frame['x']+15)
            self.assertGreaterEqual(box['y'],frame['y']+15)
            self.assertLessEqual(box['x']+box['width'],frame['x']+frame['width']-15)
            self.assertLessEqual(box['y']+box['height'],frame['y']+frame['height']-15)
            return state
        before=assert_fitted()
        x=round(before['image']['x']+before['image']['width']*.7)
        y=round(before['image']['y']+before['image']['height']*.35)
        pixel=((x-before['frame']['x']-before['x'])/before['scale'],(y-before['frame']['y']-before['y'])/before['scale'])
        page.mouse.move(x,y);page.mouse.wheel(0,-160)
        page.wait_for_function('(scale) => new DOMMatrix(getComputedStyle(document.querySelector(".moect-image-frame img")).transform).a>scale',arg=before['scale'])
        zoomed=geometry()
        self.assertAlmostEqual((x-zoomed['frame']['x']-zoomed['x'])/zoomed['scale'],pixel[0],delta=.05)
        self.assertAlmostEqual((y-zoomed['frame']['y']-zoomed['y'])/zoomed['scale'],pixel[1],delta=.05)
        page.mouse.wheel(0,160)
        page.wait_for_function('(scale) => Math.abs(new DOMMatrix(getComputedStyle(document.querySelector(".moect-image-frame img")).transform).a-scale)<.00001',arg=before['scale'])
        restored=geometry()
        self.assertAlmostEqual(restored['x'],before['x'],delta=.1)
        self.assertAlmostEqual(restored['y'],before['y'],delta=.1)
        page.mouse.down();page.mouse.move(x+60,y+35);page.mouse.up()
        dragged=geometry()
        self.assertAlmostEqual(dragged['x']-restored['x'],60,delta=1)
        self.assertAlmostEqual(dragged['y']-restored['y'],35,delta=1)
        dialog.get_by_role('button',name='适应窗口',exact=True).click();assert_fitted()
        for width in (4000,100):
            dialog.get_by_role('button',name='下一张',exact=True).click()
            page.wait_for_function('(width) => {const image=document.querySelector(".moect-image-frame img");return !image.hidden&&image.naturalWidth===width}',arg=width)
            assert_fitted()
        page.set_viewport_size({'width':390,'height':760})
        page.wait_for_function('() => {const frame=document.querySelector(".moect-image-frame"),box=frame.firstElementChild.getBoundingClientRect(),bounds=frame.getBoundingClientRect();return frame.clientWidth<390&&box.x>=bounds.x+15&&box.right<=bounds.right-15}')
        assert_fitted()
        dialog.get_by_role('button',name='上一张',exact=True).click()
        dialog.get_by_role('button',name='上一张',exact=True).click()
        page.wait_for_function('() => {const image=document.querySelector(".moect-image-frame img");return !image.hidden&&image.naturalWidth===900}')
        assert_fitted()
        page.keyboard.press('Escape')
        dialog.wait_for(state='hidden')
        # Native dialog close cleanup is queued after the dialog becomes hidden.
        page.wait_for_function('!document.documentElement.classList.contains("moect-image-open")', timeout=1000)
        self.assertEqual(errors, [])

    def test_official_filter_excludes_normal_friends_and_supports_batch_selection(self):
        page, errors = self.app()
        page.evaluate("window.__wce.setTab('sessions')")
        page.get_by_role('button', name='全部 (4)', exact=True).click()
        page.get_by_role('menuitem', name='公众号 / 服务号 (1)', exact=True).click()
        page.get_by_role('button', name='预览会话 示例公众号').wait_for()
        self.assertEqual(page.get_by_role('button', name='预览会话 示例公众号').count(), 1)
        self.assertEqual(page.get_by_role('button', name='预览会话 普通好友').count(), 0)
        self.assertEqual(errors, [])

    def test_archive_options_remember_password_presence_and_xlsx_reaches_backend(self):
        page, errors = self.app()
        page.evaluate("window.__wce.chooseItems(['friend'])")
        more = page.locator('summary').filter(has_text='更多设置')
        self.assertFalse(page.get_by_role('switch', name='导出为 ZIP 压缩包', exact=True).is_visible())
        more.click()
        self.assertTrue(more.locator('xpath=ancestor::section').get_by_role('heading', name='高级选项').is_visible())
        encrypt = page.get_by_role('switch', name='使用密码加密压缩包', exact=True)
        page.wait_for_function('() => window.__calls.includes("archive_preferences")')
        encrypt.click()
        page.get_by_label('压缩包密码', exact=True).fill('synthetic-password')
        page.keyboard.press('Escape')
        page.get_by_role('dialog', name='批量导出聊天记录', exact=True).wait_for(state='hidden')
        page.wait_for_function('() => window.__prefs.has_password')
        page.evaluate("window.__wce.chooseItems(['friend'])")
        page.locator('summary').filter(has_text='更多设置').click()
        page.wait_for_function('() => document.querySelector("input[type=password]")?.disabled===false')
        self.assertTrue(page.get_by_role('switch', name='导出为 ZIP 压缩包', exact=True).is_checked())
        self.assertTrue(page.get_by_role('switch', name='使用密码加密压缩包', exact=True).is_checked())
        self.assertEqual(page.get_by_label('压缩包密码', exact=True).input_value(), '')
        self.assertEqual(page.get_by_label('压缩包密码', exact=True).get_attribute('placeholder'), '')
        page.get_by_role('button', name='XLSX', exact=True).click()
        page.get_by_role('button', name='开始批量导出', exact=True).click()
        page.wait_for_function('() => window.__payloads.length===1')
        payload = page.evaluate('window.__payloads[0]')
        self.assertEqual(payload['format'], 'xlsx')
        self.assertEqual((payload['archiveZip'], payload['archiveEncrypt']), (True, True))
        self.assertEqual(errors, [])

    def test_account_more_settings_remember_on_escape_and_export_from_both_pages(self):
        page, errors = self.app()
        for kind, export_label in [('stickers', '导出所选分类'), ('moments', '导出所选朋友圈')]:
            page.evaluate('(tab) => window.__wce.setTab(tab)', kind)
            export = page.get_by_role('button', name=export_label, exact=True)
            export.wait_for()
            more = page.get_by_role('button', name='更多设置', exact=True)
            self.assertEqual(page.get_by_role('switch', name='导出为 ZIP 压缩包', exact=True).count(), 0)
            export_box, more_box = export.bounding_box(), more.bounding_box()
            self.assertGreater(more_box['x'], export_box['x'])
            self.assertAlmostEqual(more_box['y'], export_box['y'], delta=2)
            more.click()
            dialog = page.get_by_role('dialog', name='更多设置', exact=True)
            encrypt = dialog.get_by_role('switch', name='使用密码加密压缩包', exact=True)
            if kind == 'stickers':
                encrypt.click()
                dialog.get_by_label('压缩包密码', exact=True).fill('synthetic-assets-password')
                page.keyboard.press('Escape')
                page.wait_for_function('() => window.__prefs.has_password')
            else:
                self.assertTrue(encrypt.is_checked())
                self.assertTrue(dialog.get_by_role('switch', name='导出为 ZIP 压缩包', exact=True).is_checked())
                self.assertEqual(dialog.get_by_label('压缩包密码', exact=True).input_value(), '')
                dialog.get_by_role('button', name='完成', exact=True).click()
            dialog.wait_for(state='hidden')
            export.click()
            page.wait_for_function('(kind) => window.__assetPayloads.some(payload => payload.kind===kind)', arg=kind)
            payload = page.evaluate('window.__assetPayloads.at(-1)')
            self.assertEqual((payload['archiveZip'], payload['archiveEncrypt']), (True, True))
        self.assertEqual(errors, [])

    def test_marked_explanatory_copy_is_removed(self):
        page, errors = self.app()
        page.evaluate("window.__wce.setTab('settings')")
        for text in ('已完成的导出保留在各自的任务文件夹中。', '当前缓存位置', '迁移会复制并校验缓存。',
                     '选择你喜欢的主题色', '颜色和浅色 / 深色模式会自动记住', '回顾连接微信、浏览与导出的基本步骤。'):
            self.assertEqual(page.get_by_text(text, exact=False).count(), 0)
        page.evaluate("window.__wce.setTab('about')")
        self.assertEqual(page.get_by_text('原作者与来源详见随附的', exact=False).count(), 0)
        page.evaluate("window.__wce.setTab('moments')")
        self.assertEqual(page.get_by_text('按账号范围和日期整理朋友圈', exact=False).count(), 0)
        page.evaluate("window.__wce.chooseItems(['friend'])")
        self.assertEqual(page.get_by_text('以上设置同样作用于引用回复与合并转发中的资源', exact=False).count(), 0)
        self.assertEqual(page.get_by_text('按上方设置保存可用资源', exact=False).count(), 0)
        self.assertEqual(errors, [])

    def test_notice_width_matches_update_and_connection_hint_is_centered(self):
        page, errors = self.app()
        page.get_by_role('button', name='公告', exact=True).click()
        notice = page.get_by_role('dialog', name='Moect 公告', exact=True)
        notice.wait_for()
        page.wait_for_function('() => document.querySelector(".notice-dialog").getBoundingClientRect().width>=629.9')
        notice_width = notice.bounding_box()['width']
        self.assertAlmostEqual(notice_width, 630, delta=1)
        notice.get_by_role('button', name='关闭', exact=True).click()
        notice.wait_for(state='hidden')
        page.get_by_role('button', name='检查更新', exact=True).click()
        update = page.get_by_role('dialog', name='软件更新', exact=True)
        update.wait_for()
        page.wait_for_function('() => document.querySelector(".update-dialog").getBoundingClientRect().width>=629.9')
        self.assertAlmostEqual(update.bounding_box()['width'], notice_width, delta=1)
        update.get_by_role('button', name='关闭', exact=True).click()
        update.wait_for(state='hidden')
        page.get_by_role('button', name='连接微信', exact=True).click()
        connect = page.get_by_role('dialog', name='连接微信', exact=True)
        hint = connect.get_by_text('请先打开电脑微信并登录', exact=True)
        hint.wait_for()
        hint_box = hint.bounding_box()
        button_box = connect.get_by_role('button', name='重新检测', exact=True).bounding_box()
        self.assertAlmostEqual(hint_box['y']+hint_box['height']/2, button_box['y']+button_box['height']/2, delta=1)
        self.assertEqual(errors, [])

    def test_local_media_preview_and_storage_and_diagnostic_entries_are_operable(self):
        page, errors = self.app()
        page.evaluate("window.__wce.showPreview('friend')")
        page.get_by_alt_text('微信图片', exact=True).wait_for()
        page.get_by_role('button', name='放大图片', exact=True).click()
        self.assertTrue(page.locator('dialog[open]').is_visible())
        page.locator('dialog[open]').get_by_role('button', name='关闭', exact=True).click()
        page.keyboard.press('Escape')
        page.evaluate("window.__wce.setTab('settings')")
        page.get_by_role('button', name='清理过期快照与缓存', exact=True).click()
        page.get_by_role('button', name='开始清理', exact=True).click()
        page.get_by_role('button', name='导出诊断信息', exact=True).click()
        page.wait_for_function('() => window.__calls.includes("storage_cleanup") && window.__calls.includes("choose_diagnostics")')
        self.assertEqual(errors, [])

    def test_manual_media_retry_requests_key_recovery_and_displays_the_image(self):
        page, errors = self.app()
        page.evaluate('''() => {
          const invoke=window.pywebview.api.invoke;
          window.__mediaPayloads=[];
          window.pywebview.api.invoke=async(action,payload)=>{
            if(action==='preview_media'){
              window.__mediaPayloads.push(payload);
              return {result:payload.retry ? {kind:'image',url:window.__image} : {notice:'合成图片等待解密'}};
            }
            return invoke(action,payload);
          };
        }''')
        page.evaluate("window.__wce.showPreview('friend')")
        page.get_by_text('合成图片等待解密', exact=True).wait_for()
        self.assertEqual(page.evaluate('window.__mediaPayloads.map(row=>row.retry)'), [False])
        page.get_by_role('button', name='重试读取', exact=True).click()
        image = page.get_by_alt_text('微信图片', exact=True)
        image.wait_for()
        self.assertTrue(image.evaluate('(img) => img.complete && img.naturalWidth > 0'))
        self.assertEqual(page.evaluate('window.__mediaPayloads.map(row=>row.retry)'), [False, True])
        self.assertEqual(page.get_by_role('button', name='重试读取', exact=True).count(), 0)
        self.assertEqual(errors, [])

    def test_image_recovery_action_refreshes_preview_without_another_process_scan(self):
        page, errors = self.app()
        page.evaluate('''() => {
          const invoke=window.pywebview.api.invoke;
          window.__recovered=false;window.__recoveryTasks=[];window.__mediaRetries=[];
          window.pywebview.api.invoke=async(action,payload)=>{
            if(action==='recover_images'){
              window.__calls.push(action);
              window.__recoveryTasks=[{id:'recovery',kind:'recover_images',title:'恢复图片读取',status:'running',progress:20}];
              return {result:{job:'recovery'}};
            }
            if(action==='preview_media'){
              window.__mediaRetries.push(payload.retry);
              return {result:window.__recovered?{kind:'image',url:window.__image}:{notice:'合成图片等待恢复'}};
            }
            const response=await invoke(action,payload);
            if(action==='state'){
              response.result.tasks=structuredClone(window.__recoveryTasks);
              response.result.library.data_revision=window.__recovered?'recovered':'original';
            }
            return response;
          };
        }''')
        page.evaluate("window.__wce.setTab('sessions')")
        page.get_by_role('button', name='恢复图片读取', exact=True).click()
        page.evaluate("window.__wce.showPreview('friend')")
        page.get_by_text('合成图片等待恢复', exact=True).wait_for()
        page.wait_for_function('() => window.__wce.state.tasks.some(task=>task.kind==="recover_images")')
        page.evaluate("window.__recovered=true;window.__recoveryTasks[0].status='completed'")
        page.get_by_alt_text('微信图片', exact=True).wait_for()
        self.assertTrue(all(value is False for value in page.evaluate('window.__mediaRetries')))
        self.assertEqual(page.evaluate('window.__calls.filter(action=>action==="recover_images").length'), 1)
        self.assertEqual(errors, [])

    def test_preview_renders_all_preset_faces_in_text_quotes_and_forward_records(self):
        page, errors = self.app()
        external = []
        page.on('request', lambda request: external.append(request.url)
                if request.url.startswith(('http:', 'https:')) and not request.url.startswith('http://127.0.0.1:') else None)
        tokens = ''.join('[' + name + ']' for name in PANEL_ORDER)
        text = tokens + '\n连续[抠鼻][抠鼻] 普通[未定义] https://example.com/[捂脸] 😂 <img src=x onerror=alert(1)>'
        page.evaluate('''text => {
          const invoke=window.pywebview.api.invoke;
          window.pywebview.api.invoke=async(action,payload)=>{
            const response=await invoke(action,payload);
            if(action==='qce_preview'){
              const template=response.result.messages[0];
              response.result.messages=[
                {...template,msgId:'faces',richContent:{reply:{sender:'引用者',text:'[捂脸]'}},elements:[{textElement:{content:text}}]},
                {...template,msgId:'forward',richContent:{forward:{title:'合成转发表情',total:1,messages:[{sender:'转发者',text:'[抠鼻][捂脸]'}]}},elements:[]}
              ];
              response.result.totalCount=2;
            }
            return response;
          };
        }''', text)
        page.evaluate("window.__wce.showPreview('friend')")
        page.wait_for_function('() => document.querySelectorAll("img.moect-inline-face").length===112')
        page.wait_for_function('() => Array.from(document.querySelectorAll("img.moect-inline-face")).every(image=>image.complete&&image.naturalWidth>0)')
        self.assertTrue(set('[' + name + ']' for name in PANEL_ORDER).issubset(
            set(page.locator('img.moect-inline-face').evaluate_all('(images)=>images.map(image=>image.alt)'))))
        self.assertEqual(page.get_by_role('link', name='https://example.com/[捂脸]', exact=True).count(), 1)
        self.assertIn('普通[未定义]', page.locator('body').inner_text())
        self.assertIn('<img src=x onerror=alert(1)>', page.locator('body').inner_text())
        before = page.url
        page.get_by_alt_text('[抠鼻]', exact=True).first.click()
        self.assertEqual(page.url, before)
        page.locator('summary').filter(has_text='合成转发表情').click()
        page.wait_for_function('() => document.querySelectorAll("img.moect-inline-face").length===114')
        page.get_by_alt_text('[囧]', exact=True).dispatch_event('error')
        page.get_by_alt_text('[囧]', exact=True).wait_for(state='detached')
        self.assertIn('[囧]', page.locator('body').inner_text())
        self.assertEqual(external, [])
        self.assertEqual(errors, [])

    def test_offline_builtin_faces_preserve_search_quotes_forward_pages_and_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = Archive(fixture(root/'db', count=3))
            try:
                messages = list(archive.messages(SESSION))
                messages[0]['text'] = '[抠鼻][抠鼻][捂脸]普通[未定义] https://example.com/[捂脸]'
                messages[1].update(text='回复[囧]', quote={'text':'[捂脸]','kind':'text','sender':'引用者'})
                child = dict(messages[0], text='[庆祝][捂脸]', server_id='900')
                messages[2]['forward'] = {'title':'合成转发表情','messages':[child],'total':1}
                output = root/'output';output.mkdir()
                with patch.object(archive, 'messages', return_value=iter(messages)), patch.object(ResourceStore, 'enrich', side_effect=lambda row: row):
                    result = export_session(archive, archive.sessions()[0], {'format':'html','includeAvatars':False}, output, ASSETS, lambda _:None, lambda:None)
                context = self.browser.new_context()
                try:
                    page = context.new_page();errors=[];external=[]
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: external.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
                    page.goto(Path(result['path']).as_uri())
                    page.wait_for_function('() => !reading && total===3')
                    self.assertEqual(page.locator('img.moect-inline-face').count(), 7)
                    page.wait_for_function('() => Array.from(document.querySelectorAll("img.moect-inline-face")).every(image=>image.complete&&image.naturalWidth>0)')
                    page.get_by_alt_text('[抠鼻]', exact=True).first.click()
                    self.assertFalse(page.get_by_role('dialog', name='图片预览', exact=True).is_visible())
                    page.locator('#search').fill('[抠鼻]')
                    page.get_by_role('button', name='筛选', exact=True).click()
                    page.wait_for_function('() => !reading && total===1')
                    self.assertEqual(page.locator('img.moect-inline-face').count(), 3)
                    page.get_by_role('button', name='显示全部', exact=True).click()
                    page.wait_for_function('() => !reading && total===3')
                    href = page.locator('.forward-card').get_attribute('href')
                    forwarded = context.new_page()
                    forwarded.goto((Path(result['path']).parent/href).as_uri())
                    forwarded.wait_for_function('() => !reading && total===1')
                    self.assertEqual(forwarded.locator('img.moect-inline-face').count(), 2)
                    forwarded.wait_for_function('() => Array.from(document.querySelectorAll("img.moect-inline-face")).every(image=>image.complete&&image.naturalWidth>0)')
                    page.get_by_alt_text('[囧]', exact=True).dispatch_event('error')
                    page.get_by_alt_text('[囧]', exact=True).wait_for(state='detached')
                    self.assertIn('[囧]', page.locator('#messages').inner_text())
                    self.assertEqual(external, [])
                    self.assertEqual(errors, [])
                finally:
                    context.close()
            finally:
                archive.close()

    def test_moments_preview_and_offline_body_comments_use_preset_faces(self):
        page, errors = self.app()
        page.evaluate('''() => {
          const invoke=window.pywebview.api.invoke;
          window.pywebview.api.invoke=async(action,payload)=>{
            const response=await invoke(action,payload);
            if(action==='account_assets')response.result.posts=[{id:'face-post',text:'动态[捂脸][囧]',author:'synthetic',create_time:1800000000}];
            return response;
          };
          window.__wce.setTab('moments');
        }''')
        page.wait_for_function('() => document.querySelectorAll("img.moect-inline-face").length===2')
        page.wait_for_function('() => Array.from(document.querySelectorAll("img.moect-inline-face")).every(image=>image.complete&&image.naturalWidth>0)')
        self.assertEqual(errors, [])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            data={'category':'moments','name':'合成朋友圈','owner':{'name':'合成用户'},'exported_at':'2026-11-10',
                  'items':[{'id':'1','author':'synthetic','text':'动态[捂脸][囧] [未知] https://example.com/[微笑] <script>unsafe</script>',
                            'comments':[{'uid':'commenter','name':'评论者','text':'回复[庆祝][抠鼻]'}]}]}
            write_collection(root, ASSETS, data)
            context=self.browser.new_context();self.addCleanup(context.close)
            offline=context.new_page();external=[];archive_errors=[]
            offline.on('pageerror', lambda error: archive_errors.append(str(error)))
            offline.on('request', lambda request: external.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
            offline.goto((root/'index.html').as_uri())
            offline.wait_for_function('() => document.querySelectorAll("img.moect-inline-face").length===4')
            offline.wait_for_function('() => Array.from(document.querySelectorAll("img.moect-inline-face")).every(image=>image.complete&&image.naturalWidth>0)')
            self.assertEqual(offline.locator('.comment-text img.moect-inline-face').count(),2)
            self.assertIn('<script>unsafe</script>',offline.locator('#items').inner_text())
            offline.get_by_alt_text('[囧]',exact=True).click()
            self.assertFalse(offline.get_by_role('dialog',name='图片预览',exact=True).is_visible())
            offline.locator('#query').fill('[捂脸]')
            self.assertEqual(offline.locator('article.post').count(),1)
            offline.locator('#query').fill('不存在的正文')
            self.assertEqual(offline.locator('article.post').count(),0)
            offline.locator('#query').fill('')
            # Reject tampered external/traversing registry entries instead of fetching them.
            offline.evaluate('''() => {
              window.MoectInlineFaces['[捂脸]']='https://example.invalid/face.png';
              window.MoectInlineFaces['[囧]']='../outside.png';
              const target=document.createElement('div');target.id='tampered';document.body.append(target);
              MoectFaces.append(target,'[捂脸][囧]');
            }''')
            self.assertEqual(offline.locator('#tampered').inner_text(),'[捂脸][囧]')
            self.assertEqual(offline.locator('#tampered img').count(),0)
            self.assertEqual(external,[])
            self.assertEqual(archive_errors,[])

    def test_offline_emoji_uses_one_image_lightbox_and_direct_page_jump(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = Archive(fixture(root/'db', count=1601))
            try:
                original = list(archive.messages(SESSION))
                original[-1].update(kind='emoji', text='[动画表情]', resources=[
                    {'kind':'image','status':'available','role':'main','path':'main.png'},
                    {'kind':'image','status':'available','role':'thumbnail','path':'thumb.png'}])
                output = root/'output';output.mkdir()
                with patch.object(archive, 'messages', return_value=iter(original)), patch.object(ResourceStore, 'enrich', side_effect=lambda message: dict(message)):
                    result = export_session(archive, archive.sessions()[0], {'format':'html','includeAvatars':False}, output, ASSETS, lambda _: None, lambda: None)
                folder = Path(result['path']).parent
                (folder/'main.png').write_bytes(PNG);(folder/'thumb.png').write_bytes(PNG)
                context = self.browser.new_context()
                try:
                    page = context.new_page();errors=[]
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(Path(result['path']).as_uri())
                    page.wait_for_function('() => !reading && total===1601')
                    self.assertEqual(page.locator('#messages img.emoji').count(), 1)
                    before = page.url
                    page.get_by_alt_text('表情', exact=True).click()
                    self.assertTrue(page.get_by_role('dialog', name='图片预览', exact=True).is_visible())
                    self.assertEqual(page.url, before)
                    page.keyboard.press('Escape')
                    jump = page.get_by_label('跳转页码', exact=True)
                    self.assertTrue(jump.is_visible())
                    self.assertLessEqual(jump.bounding_box()['width'], 54)
                    numbers, controls = page.locator('#pagination').bounding_box(), page.locator('#page-jump-controls').bounding_box()
                    self.assertGreaterEqual(controls['x'], numbers['x'] + numbers['width'])
                    self.assertAlmostEqual(controls['y'], numbers['y'], delta=2)
                    page.evaluate('total=19*80;renderPagination()')
                    self.assertFalse(jump.is_visible())
                    page.evaluate('total=20*80;renderPagination()')
                    self.assertTrue(jump.is_visible())
                    page.evaluate('total=1601;renderPagination()')
                    page.get_by_label('跳转页码', exact=True).fill('2')
                    page.get_by_role('button', name='跳转', exact=True).click()
                    page.wait_for_function('() => !reading && page===1')
                    self.assertEqual(page.locator('#messages .row').count(), 80)
                    page.get_by_label('跳转页码', exact=True).fill('99999')
                    page.get_by_role('button', name='跳转', exact=True).click()
                    self.assertIn('有效页码', page.locator('#notice').text_content())
                    page.locator('#search').fill('unmatched-synthetic-filter')
                    page.get_by_role('button', name='筛选', exact=True).click()
                    page.wait_for_function('() => !reading && total===0')
                    self.assertFalse(jump.is_visible())
                    self.assertEqual(errors, [])
                finally:
                    context.close()
            finally:
                archive.close()
