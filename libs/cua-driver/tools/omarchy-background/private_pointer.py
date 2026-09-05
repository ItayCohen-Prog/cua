"""Persistent XTEST pointer for a task's private display, never the host display."""
import ctypes
import math
import time

NAMES={'mouse_button_down','mouse_drag','mouse_button_up','move_cursor'}

class Pointer:
    def __init__(self, rpc):
        self.rpc=rpc
        self.scales={}
        self.held=None
        self.x=ctypes.CDLL('libX11.so.6')
        self.xt=ctypes.CDLL('libXtst.so.6')
        self.x.XOpenDisplay.restype=ctypes.c_void_p
        self.x.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]
        self.x.XCloseDisplay.argtypes=[ctypes.c_void_p]
        self.xt.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
        self.xt.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
        self.display=self.x.XOpenDisplay(None)
        if not self.display:raise RuntimeError('Cannot open private XTEST display')

    def observe(self,result):
        data=result.get('structuredContent',{})
        if data.get('window_id') and data.get('screenshot_width') and data.get('window_bounds'):
            bounds=data['window_bounds']
            self.scales[data['window_id']]=(bounds['width']/data['screenshot_width'],bounds['height']/data['screenshot_height'])

    def call_driver(self,name,args):
        result=self.rpc.call('tools/call',{'name':name,'arguments':args})
        if result.get('isError'):raise RuntimeError(str(result))
        return result

    def target(self,args):
        target=self.held['target'] if self.held else {k:args[k] for k in ('pid','window_id')}
        for key in ('pid','window_id'):
            if key in args and args[key]!=target[key]:raise ValueError('Held pointer target cannot change')
        windows=self.call_driver('list_windows',{})['structuredContent']['windows']
        window=next((w for w in windows if all(w[k]==v for k,v in target.items())),None)
        if not window:raise ValueError('Pointer target is stale; refresh list_windows')
        return target,window

    def point(self,args,window):
        scale=self.scales.get(window['window_id'],(1,1))
        x,y=float(args['x'])*scale[0],float(args['y'])*scale[1]
        if not (math.isfinite(x) and math.isfinite(y) and 0<=x<window['width'] and 0<=y<window['height']):
            raise ValueError('Point must be within the captured client window')
        return round(window['x']+x),round(window['y']+y)

    def move(self,point):
        self.xt.XTestFakeMotionEvent(self.display,-1,*point,0)
        self.x.XSync(self.display,0)

    def release(self):
        if self.held:
            self.xt.XTestFakeButtonEvent(self.display,self.held['button'],0,0)
            self.x.XSync(self.display,0)
            self.held=None

    def call(self,name,args):
        if args.get('from_zoom'):raise ValueError('Persistent pointer uses window captures, not zoom coordinates')
        if name in ('mouse_drag','mouse_button_up') and not self.held:raise ValueError('No held button; call mouse_button_down first')
        if name in ('move_cursor','mouse_button_down') and self.held:raise ValueError('Release the held button before another pointer operation')
        try:
            target,window=self.target(args)
            if name in ('move_cursor','mouse_button_down'):
                button={'left':1,'middle':2,'right':3}.get(args.get('button','left'))
                if not button:raise ValueError('Unknown mouse button')
                point=self.point(args,window)
                self.call_driver('bring_to_front',target)
                self.move(point)
                if name=='mouse_button_down':
                    self.xt.XTestFakeButtonEvent(self.display,button,1,0)
                    self.x.XSync(self.display,0)
                    self.held={'target':target,'button':button,'point':point}
            elif name=='mouse_drag':
                point=self.point(args,window)
                steps=int(args.get('steps',20));duration=int(args.get('duration_ms',500))
                if not 1<=steps<=200 or not 0<=duration<=10000:raise ValueError('Drag duration or steps out of range')
                start=self.held['point']
                for step in range(1,steps+1):
                    self.move(tuple(round(a+(b-a)*step/steps) for a,b in zip(start,point)))
                    time.sleep(duration/steps/1000)
                self.held['point']=point
            else:
                if 'x' in args or 'y' in args:self.move(self.point(args,window))
                self.release()
            return {'content':[{'type':'text','text':f'{name} delivered to the private display; held={bool(self.held)}'}],
                    'structuredContent':{'held':bool(self.held),'delivery':{'mode':'foreground'},'route':'global_input','effect':'unverifiable'}}
        except BaseException:
            self.release()
            raise

    def close(self):
        self.release()
        self.x.XCloseDisplay(self.display)


def schemas():
    target={'pid':{'type':'integer'},'window_id':{'type':'integer'}}
    point={'x':{'type':'number'},'y':{'type':'number'}}
    definitions=[
        ('move_cursor','Move/hover the private pointer over a captured window.',{**target,**point},['pid','window_id','x','y']),
        ('mouse_button_down','Press and hold one private XTEST button. Coordinates match the latest window screenshot.',
         {**target,**point,'button':{'enum':['left','middle','right'],'type':'string'}},['pid','window_id','x','y']),
        ('mouse_drag','Move the held private pointer in the same window without releasing it.',
         {**target,**point,'duration_ms':{'type':'integer','minimum':0,'maximum':10000},'steps':{'type':'integer','minimum':1,'maximum':200}},['x','y']),
        ('mouse_button_up','Release the held private pointer, optionally at new coordinates.',{**target,**point},[]),
    ]
    return {name:{'name':name,'description':description,'inputSchema':{'type':'object','properties':props,
                 'required':required,'additionalProperties':False}} for name,description,props,required in definitions}
