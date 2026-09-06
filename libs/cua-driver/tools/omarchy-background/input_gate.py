"""Gate physical Wayland input inside the private X server, leaving XTEST intact."""
import ctypes as C
import os


class Device(C.Structure):
    _fields_ = [('id', C.c_int), ('name', C.c_char_p), ('use', C.c_int),
                ('attachment', C.c_int), ('enabled', C.c_int),
                ('num_classes', C.c_int), ('classes', C.c_void_p)]


class InputGate:
    def __init__(self):
        # This module is only called in the PID/mount namespace worker.
        if os.environ.get('WAYLAND_DISPLAY') or not os.environ.get('DISPLAY', '').startswith(':62'):
            raise RuntimeError('Input gate requires the private display')
        self.x = C.CDLL('libX11.so.6')
        self.xi = C.CDLL('libXi.so.6')
        self.x.XOpenDisplay.restype = C.c_void_p
        self.x.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
        self.x.XInternAtom.restype = C.c_ulong
        self.x.XSync.argtypes = [C.c_void_p, C.c_int]
        self.x.XCloseDisplay.argtypes = [C.c_void_p]
        self.xi.XIQueryDevice.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_int)]
        self.xi.XIQueryDevice.restype = C.POINTER(Device)
        self.xi.XIFreeDeviceInfo.argtypes = [C.POINTER(Device)]
        self.xi.XIChangeProperty.argtypes = [C.c_void_p, C.c_int, C.c_ulong,
                                            C.c_ulong, C.c_int, C.c_int,
                                            C.POINTER(C.c_ubyte), C.c_int]
        self.d = self.x.XOpenDisplay(None)
        if not self.d:
            raise RuntimeError('Cannot open private input display')
        self.atom = self.x.XInternAtom(self.d, b'Device Enabled', False)
        self.agent = True
        self.apply(True)

    def devices(self):
        n = C.c_int()
        ptr = self.xi.XIQueryDevice(self.d, 0, C.byref(n))
        if not ptr:
            raise RuntimeError('Cannot inspect private input devices')
        try:
            return [{'id': ptr[i].id, 'name': ptr[i].name.decode(),
                     'use': ptr[i].use, 'enabled': bool(ptr[i].enabled)} for i in range(n.value)]
        finally:
            self.xi.XIFreeDeviceInfo(ptr)

    def apply(self, agent):
        for device in self.devices():
            if device['use'] in (1, 2) or 'XTEST' in device['name']:
                continue
            if not device['name'].lower().startswith('xwayland-'):
                raise RuntimeError('Unclassified private input device: '+device['name'])
            if device['enabled'] != (not agent):
                value = C.c_ubyte(not agent)
                self.xi.XIChangeProperty(self.d, device['id'], self.atom, 19, 8, 0, C.byref(value), 1)
        self.x.XSync(self.d, False)
        devices = self.devices()
        if any(d['enabled'] == agent for d in devices if d['name'].lower().startswith('xwayland-')):
            raise RuntimeError('Private physical input state failed verification')
        self.agent = agent
        return devices

    def close(self):
        self.x.XCloseDisplay(self.d)
