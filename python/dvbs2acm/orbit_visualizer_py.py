"""
orbit_visualizer_py.py — Real-time LEO satellite visualizer for GNU Radio

Three-tab floating window:
  Tab 0 – Earth View    : QPainter orthographic projection (satellite eye view)
  Tab 1 – Link Analysis : matplotlib dark-theme analytics (SNR, MODCOD dist, ACM gain)
  Tab 2 – Sky View      : polar sky plot (elevation rings, MODCOD-coloured trail)

Receives:
  channel_state_in ← dvbs2acm_leo_channel.channel_state
  modcod_in        ← acm_controller.modcod_out  (optional)

Sync fix: trail stored as 4-tuple (az, el, mc_id, pass_frac) so new-pass
detection correctly checks pass_frac, not mc_id.
QTimer at 100 ms forces periodic redraws even if signal delivery is bursty.
"""

import math
import threading

import numpy as np
import gnuradio.gr as gr
import pmt

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    import matplotlib.gridspec as gridspec
    import matplotlib.patches as mpatches
    _MPL_AVAILABLE = True
except ImportError:
    _MPL_AVAILABLE = False

try:
    from PyQt5.QtWidgets import QOpenGLWidget
    from OpenGL import GL as gl, GLU as glu
    from PIL import Image
    _GL_AVAILABLE = True
except ImportError:
    _GL_AVAILABLE = False

# ── Earth / orbit constants ───────────────────────────────────────────────────

R_EARTH_KM = 6371.0

# ── MODCOD colour palette ─────────────────────────────────────────────────────

_FAMILY_COLORS = {
    'QPSK':   (0x27, 0xAE, 0x60),   # green
    '8PSK':   (0x29, 0x80, 0xB9),   # blue
    '16APSK': (0xF3, 0x9C, 0x12),   # amber
    '32APSK': (0xE7, 0x4C, 0x3C),   # red
    'NONE':   (0x7F, 0x8C, 0x8D),   # grey
}

_MODCOD_BITS_PER_SYMBOL = {
    1:0.490, 2:0.657, 3:0.789, 4:0.989, 5:1.188, 6:1.322,
    7:1.487, 8:1.655, 9:1.766, 10:1.989,
    11:2.228, 12:2.479, 13:2.637, 14:2.967, 15:3.300,
    16:3.289, 17:3.707, 18:3.972, 19:4.206, 20:4.453, 21:4.742,
    22:4.420, 23:4.674, 24:5.115, 25:5.523, 26:5.836,
    27:5.163, 28:5.836,
}

_MODCOD_MIN_SNR_DB = {
    1:-2.35, 2:-1.24, 3:-0.30, 4:1.00, 5:2.23, 6:3.10,
    7:4.03, 8:4.68, 9:5.18, 10:6.20,
    11:5.50, 12:6.62, 13:7.91, 14:9.35, 15:10.69,
    16:8.97, 17:10.21, 18:11.03, 19:11.61, 20:12.89, 21:13.13,
    22:11.61, 23:12.73, 24:13.64, 25:14.28, 26:14.71,
    27:13.05, 28:14.81,
}


def _modcod_family(mid: int) -> str:
    if   mid <= 10: return 'QPSK'
    elif mid <= 15: return '8PSK'
    elif mid <= 21: return '16APSK'
    elif mid <= 28: return '32APSK'
    return 'NONE'


def _qcolor(mid: int) -> 'QtGui.QColor':
    r, g, b = _FAMILY_COLORS[_modcod_family(mid)]
    return QtGui.QColor(r, g, b)


def _qcolor_rgb(mid: int):
    return _FAMILY_COLORS[_modcod_family(mid)]


def _family_mpl_color(family: str) -> str:
    """Return matplotlib hex color string for a MODCOD family."""
    r, g, b = _FAMILY_COLORS.get(family, _FAMILY_COLORS['NONE'])
    return f'#{r:02x}{g:02x}{b:02x}'


# ── Orbital geometry helpers ──────────────────────────────────────────────────

def _subsatellite_point(lat_gs_r: float, lon_gs_r: float,
                        az_r: float, el_r: float,
                        alt_km: float = 500.0):
    """
    Return (lat_s_r, lon_s_r) for the subsatellite point.
    Uses spherical-Earth geometry from ground-station az/el.
    """
    Re = R_EARTH_KM
    nadir_r = math.asin(min(1.0, Re * math.cos(el_r) / (Re + alt_km)))
    theta_r = max(0.0, math.pi / 2.0 - el_r - nadir_r)
    lat_s = math.asin(
        math.sin(lat_gs_r) * math.cos(theta_r)
        + math.cos(lat_gs_r) * math.sin(theta_r) * math.cos(az_r)
    )
    dlon = math.atan2(
        math.sin(az_r) * math.sin(theta_r) * math.cos(lat_gs_r),
        math.cos(theta_r) - math.sin(lat_gs_r) * math.sin(lat_s),
    )
    return lat_s, lon_gs_r + dlon


def _footprint_circle(lat_s_r: float, lon_s_r: float,
                      alt_km: float = 500.0, n: int = 72):
    """
    Return list of (lat_r, lon_r) for the satellite visibility footprint.
    """
    Re = R_EARTH_KM
    rho = math.acos(Re / (Re + alt_km))  # Earth central angle to horizon
    pts = []
    for i in range(n + 1):
        az = 2.0 * math.pi * i / n
        lat = math.asin(
            math.sin(lat_s_r) * math.cos(rho)
            + math.cos(lat_s_r) * math.sin(rho) * math.cos(az)
        )
        dlon = math.atan2(
            math.sin(az) * math.sin(rho) * math.cos(lat_s_r),
            math.cos(rho) - math.sin(lat_s_r) * math.sin(lat),
        )
        pts.append((lat, lon_s_r + dlon))
    return pts


def _ortho_project(lat_r, lon_r, center_lat_r, center_lon_r):
    """Returns (x_norm, y_norm, visible) in range [-1,1]."""
    x = math.cos(lat_r) * math.sin(lon_r - center_lon_r)
    y = (math.sin(lat_r) * math.cos(center_lat_r)
         - math.cos(lat_r) * math.sin(center_lat_r) * math.cos(lon_r - center_lon_r))
    dot = (math.sin(center_lat_r) * math.sin(lat_r)
           + math.cos(center_lat_r) * math.cos(lat_r) * math.cos(lon_r - center_lon_r))
    return x, y, dot > -0.01


def _ll2xyz(lat_deg, lon_deg, r=1.002):
    """Convert geographic lat/lon to OpenGL XYZ on sphere of radius r."""
    # theta: 0 at 180°W, pi at 0°E (matches equirectangular texture left-edge=180°W)
    theta = math.radians(lon_deg + 180.0)
    phi   = math.radians(90.0 - lat_deg)
    x = r * math.sin(phi) * math.cos(theta)
    y = r * math.cos(phi)
    z = r * math.sin(phi) * math.sin(theta)
    return x, y, z


# ── Simplified continent outlines ─────────────────────────────────────────────
# Format: (lon_deg, lat_deg) tuples

_LAND_POLYS = [
    # North America (simplified)
    [(-168,72),(-141,60),(-124,49),(-118,34),(-118,18),(-90,16),(-77,8),
     (-77,25),(-80,32),(-76,45),(-66,47),(-52,47),(-52,63),(-60,68),
     (-80,72),(-95,74),(-110,78),(-140,78),(-168,72)],
    # South America
    [(-81,8),(-78,2),(-73,-5),(-70,-15),(-66,-20),(-57,-25),(-48,-28),
     (-43,-23),(-35,-8),(-35,0),(-50,0),(-63,8),(-73,11),(-81,8)],
    # Europe
    [(2,51),(10,54),(18,55),(25,60),(28,70),(24,76),(10,72),(5,62),(0,51),
     (2,44),(8,44),(15,47),(22,44),(28,42),(28,35),(20,35),(10,38),(0,40),
     (-9,38),(-9,44),(-4,48),(2,51)],
    # Africa
    [(-17,15),(0,8),(15,8),(30,3),(38,12),(42,12),(42,8),(36,-22),(28,-35),
     (18,-35),(15,-28),(8,-22),(8,-8),(0,0),(-5,5),(-17,15)],
    # Asia (very rough)
    [(28,42),(38,37),(42,38),(48,30),(55,25),(58,20),(72,22),(80,15),
     (100,5),(110,0),(120,10),(130,35),(140,40),(140,50),(130,60),
     (120,70),(100,72),(90,78),(70,78),(60,68),(55,60),(50,55),(40,55),
     (30,60),(25,65),(28,70),(28,60),(38,50),(38,42),(28,42)],
    # Australia
    [(114,-22),(122,-18),(135,-12),(137,-12),(136,-22),(137,-35),(145,-38),
     (150,-35),(152,-28),(148,-18),(144,-14),(135,-14),(128,-14),(120,-22),
     (114,-22)],
    # Greenland (rough)
    [(-52,83),(-18,78),(-20,70),(-28,65),(-44,60),(-52,63),(-60,68),
     (-52,78),(-40,83),(-52,83)],
    # UK+Ireland rough
    [(-6,56),(0,51),(2,51),(0,54),(-6,58),(-8,56),(-10,52),(-6,51),(-3,51),
     (-3,53),(-6,56)],
    # Japan (very rough)
    [(141,41),(145,44),(146,44),(141,38),(137,35),(136,34),(130,32),(130,34),
     (133,36),(137,38),(141,41)],
    # New Zealand (rough)
    [(172,-34),(174,-36),(174,-40),(170,-43),(168,-46),(166,-46),(168,-43),
     (171,-38),(173,-36),(172,-34)],
]


# ── TAB 0: 3D Globe (OpenGL) ──────────────────────────────────────────────────

if _GL_AVAILABLE:
    class _GlobeWidget(QOpenGLWidget):
        """Interactive 3D OpenGL globe with texture, satellite marker and HUD overlay."""

        def __init__(self, state, parent=None):
            super().__init__(parent)
            self._s = state
            self._rot_x = 20.0
            self._rot_y = 0.0
            self._last_mouse = None
            self._zoom = 1.0
            self._texture_id = None
            self._sphere_verts = None
            self._sphere_norms = None
            self._sphere_texuv = None
            self._sphere_indices = None
            self._star_verts = None
            self.setMouseTracking(True)
            # HUD data cached from last paintGL call, drawn in paintEvent
            self._hud_data = None

        def initializeGL(self):
            gl.glClearColor(0.01, 0.01, 0.02, 1.0)
            gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glEnable(gl.GL_TEXTURE_2D)
            gl.glEnable(gl.GL_LIGHTING)
            gl.glEnable(gl.GL_LIGHT0)
            gl.glEnable(gl.GL_COLOR_MATERIAL)
            gl.glColorMaterial(gl.GL_FRONT_AND_BACK, gl.GL_AMBIENT_AND_DIFFUSE)
            gl.glShadeModel(gl.GL_SMOOTH)

            # Light from upper-right (simulates Sun)
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_POSITION, [5.0, 3.0, 5.0, 0.0])
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_DIFFUSE,  [1.1, 1.1, 1.0, 1.0])
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_AMBIENT,  [0.18, 0.18, 0.22, 1.0])
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_SPECULAR, [0.4, 0.4, 0.4, 1.0])

            gl.glMaterialfv(gl.GL_FRONT, gl.GL_SPECULAR, [0.15, 0.15, 0.15, 1.0])
            gl.glMaterialf(gl.GL_FRONT, gl.GL_SHININESS, 12.0)

            self._load_texture()
            self._build_sphere()
            self._build_stars()

        def _load_texture(self):
            import os
            cache_path = os.path.expanduser('~/.cache/gr-dvbs2acm/earth_texture.jpg')
            if not os.path.exists(cache_path):
                # Try to download in background; proceed without texture for now
                import threading
                threading.Thread(target=self._download_texture, args=(cache_path,), daemon=True).start()
                return
            try:
                img = Image.open(cache_path).convert('RGB')
                img = img.transpose(Image.FLIP_TOP_BOTTOM)  # OpenGL texture origin is bottom-left
                img_data = np.array(img, dtype=np.uint8)
                W, H = img.size

                self._texture_id = gl.glGenTextures(1)
                gl.glBindTexture(gl.GL_TEXTURE_2D, self._texture_id)
                gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR_MIPMAP_LINEAR)
                gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
                gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_REPEAT)
                gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
                glu.gluBuild2DMipmaps(gl.GL_TEXTURE_2D, gl.GL_RGB, W, H,
                                       gl.GL_RGB, gl.GL_UNSIGNED_BYTE, img_data.tobytes())
            except Exception as e:
                print(f"[GlobeWidget] Texture load failed: {e}")

        def _download_texture(self, cache_path):
            import urllib.request, os
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            url = 'https://raw.githubusercontent.com/turban/webgl-earth/master/images/2_no_clouds_4k.jpg'
            try:
                data = urllib.request.urlopen(url, timeout=15).read()
                img = Image.open(__import__('io').BytesIO(data)).convert('RGB')
                img = img.resize((1024, 512), Image.LANCZOS)
                img.save(cache_path, 'JPEG', quality=88)
                # Schedule texture reload on GL thread
                from PyQt5 import QtCore
                QtCore.QMetaObject.invokeMethod(self, '_reload_texture_slot', QtCore.Qt.QueuedConnection)
            except Exception:
                pass

        @QtCore.pyqtSlot()
        def _reload_texture_slot(self):
            self.makeCurrent()
            self._load_texture()
            self.doneCurrent()
            self.update()

        def _build_sphere(self):
            slices, stacks = 72, 36
            verts, norms, texuv = [], [], []
            for i in range(stacks + 1):
                phi = math.pi * i / stacks         # 0 (north) to pi (south)
                cp, sp = math.cos(phi), math.sin(phi)
                for j in range(slices + 1):
                    theta = 2.0 * math.pi * j / slices  # 0 to 2*pi
                    ct, st = math.cos(theta), math.sin(theta)
                    x, y, z = sp * ct, cp, sp * st
                    verts += [x, y, z]
                    norms += [x, y, z]
                    texuv += [j / slices, 1.0 - i / stacks]  # v flipped: 1=north, 0=south
            indices = []
            for i in range(stacks):
                for j in range(slices):
                    a = i * (slices + 1) + j
                    b = a + slices + 1
                    indices += [a, b, a+1, b, b+1, a+1]
            self._sphere_verts   = np.array(verts,   dtype=np.float32)
            self._sphere_norms   = np.array(norms,   dtype=np.float32)
            self._sphere_texuv   = np.array(texuv,   dtype=np.float32)
            self._sphere_indices = np.array(indices, dtype=np.uint32)

        def _build_stars(self):
            rng = np.random.default_rng(42)
            n = 5000
            theta = rng.uniform(0, 2*math.pi, n)
            phi   = np.arccos(1 - 2 * rng.uniform(0, 1, n))
            r = rng.uniform(45, 55, n)
            x = r * np.sin(phi) * np.cos(theta)
            y = r * np.cos(phi)
            z = r * np.sin(phi) * np.sin(theta)
            self._star_verts = np.column_stack([x, y, z]).astype(np.float32).ravel()

        def resizeGL(self, w, h):
            gl.glViewport(0, 0, w, max(1, h))
            gl.glMatrixMode(gl.GL_PROJECTION)
            gl.glLoadIdentity()
            glu.gluPerspective(45.0, w / max(1, h), 0.05, 200.0)
            gl.glMatrixMode(gl.GL_MODELVIEW)

        def paintGL(self):
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            gl.glLoadIdentity()

            # Camera: zoom out and tilt
            r_cam = self._zoom * 2.8
            gl.glTranslatef(0.0, 0.0, -r_cam)
            gl.glRotatef(self._rot_x, 1.0, 0.0, 0.0)
            gl.glRotatef(self._rot_y, 0.0, 1.0, 0.0)

            # Read state (fast, under lock)
            with self._s._lock:
                sat_lat   = math.degrees(self._s._sat_lat_r)
                sat_lon   = math.degrees(self._s._sat_lon_r)
                alt_km    = self._s._alt_km
                mc_id     = self._s._mc_id
                active    = self._s._active
                el        = self._s._el
                snr       = self._s._snr
                pf        = self._s._pf
                rtt       = self._s._rtt
                dop       = self._s._dop
                mc_name   = self._s._mc_name
                gs_lat    = math.degrees(self._s._gs_lat)
                gs_lon    = math.degrees(self._s._gs_lon)
                geo_trail = list(self._s._geo_trail)

            # 1. Stars (no lighting, no texture, point rendering)
            gl.glDisable(gl.GL_LIGHTING)
            gl.glDisable(gl.GL_TEXTURE_2D)
            gl.glPointSize(1.2)
            gl.glColor3f(1.0, 1.0, 1.0)
            if self._star_verts is not None:
                gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
                gl.glVertexPointer(3, gl.GL_FLOAT, 0, self._star_verts)
                gl.glDrawArrays(gl.GL_POINTS, 0, len(self._star_verts) // 3)
                gl.glDisableClientState(gl.GL_VERTEX_ARRAY)

            # 2. Earth sphere
            gl.glEnable(gl.GL_LIGHTING)
            gl.glEnable(gl.GL_TEXTURE_2D)
            gl.glColor3f(1.0, 1.0, 1.0)
            if self._texture_id:
                gl.glBindTexture(gl.GL_TEXTURE_2D, self._texture_id)
            else:
                gl.glDisable(gl.GL_TEXTURE_2D)
                gl.glColor3f(0.1, 0.25, 0.4)  # ocean blue fallback

            if self._sphere_verts is not None:
                gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
                gl.glEnableClientState(gl.GL_NORMAL_ARRAY)
                gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)
                gl.glVertexPointer(3,   gl.GL_FLOAT, 0, self._sphere_verts)
                gl.glNormalPointer(     gl.GL_FLOAT, 0, self._sphere_norms)
                gl.glTexCoordPointer(2, gl.GL_FLOAT, 0, self._sphere_texuv)
                gl.glDrawElements(gl.GL_TRIANGLES, len(self._sphere_indices),
                                  gl.GL_UNSIGNED_INT, self._sphere_indices)
                gl.glDisableClientState(gl.GL_VERTEX_ARRAY)
                gl.glDisableClientState(gl.GL_NORMAL_ARRAY)
                gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
            gl.glEnable(gl.GL_TEXTURE_2D)

            # 3. Atmosphere glow (back-face only = outer shell)
            gl.glDisable(gl.GL_TEXTURE_2D)
            gl.glDisable(gl.GL_LIGHTING)
            gl.glEnable(gl.GL_BLEND)
            gl.glCullFace(gl.GL_FRONT)
            gl.glEnable(gl.GL_CULL_FACE)
            gl.glColor4f(0.2, 0.5, 1.0, 0.08)
            self._draw_simple_sphere(1.025, 32, 16)
            gl.glCullFace(gl.GL_BACK)
            gl.glDisable(gl.GL_CULL_FACE)

            # 4. Footprint circle (dashed white ring on Earth surface)
            if active:
                gl.glLineWidth(1.0)
                gl.glColor4f(1.0, 1.0, 1.0, 0.3)
                self._draw_footprint(sat_lat, sat_lon, alt_km)

            # 5. Ground track (MODCOD-coloured line)
            gl.glLineWidth(2.0)
            if len(geo_trail) >= 2:
                for i in range(1, len(geo_trail)):
                    la0, lo0, mc0 = geo_trail[i-1]
                    la1, lo1, mc1 = geo_trail[i]
                    r_rgb = _qcolor_rgb(mc1)
                    gl.glColor3f(r_rgb[0]/255, r_rgb[1]/255, r_rgb[2]/255)
                    gl.glBegin(gl.GL_LINES)
                    gl.glVertex3f(*_ll2xyz(la0, lo0, 1.002))
                    gl.glVertex3f(*_ll2xyz(la1, lo1, 1.002))
                    gl.glEnd()

            # 6. Nadir line (from satellite to surface)
            if active:
                sat_r = 1.0 + alt_km / R_EARTH_KM
                sx, sy, sz = _ll2xyz(sat_lat, sat_lon, sat_r)
                gx, gy, gz = _ll2xyz(sat_lat, sat_lon, 1.001)
                gl.glColor4f(1.0, 1.0, 1.0, 0.25)
                gl.glLineWidth(1.0)
                gl.glBegin(gl.GL_LINES)
                gl.glVertex3f(sx, sy, sz)
                gl.glVertex3f(gx, gy, gz)
                gl.glEnd()

            # 7. Ground station marker (yellow spike)
            gsx, gsy, gsz = _ll2xyz(gs_lat, gs_lon, 1.0)
            gsx_t, gsy_t, gsz_t = _ll2xyz(gs_lat, gs_lon, 1.018)
            gl.glLineWidth(2.0)
            gl.glColor3f(1.0, 0.85, 0.0)
            gl.glBegin(gl.GL_LINES)
            gl.glVertex3f(gsx, gsy, gsz)
            gl.glVertex3f(gsx_t, gsy_t, gsz_t)
            gl.glEnd()
            # Small circle marker at base
            gl.glPointSize(6.0)
            gl.glBegin(gl.GL_POINTS)
            gl.glVertex3f(gsx_t, gsy_t, gsz_t)
            gl.glEnd()

            # 8. Satellite marker (glowing sphere)
            if active:
                sat_r = 1.0 + alt_km / R_EARTH_KM
                sx, sy, sz = _ll2xyz(sat_lat, sat_lon, sat_r)
                r_rgb = _qcolor_rgb(mc_id)

                gl.glPushMatrix()
                gl.glTranslatef(sx, sy, sz)

                # Glow halo
                gl.glColor4f(r_rgb[0]/255, r_rgb[1]/255, r_rgb[2]/255, 0.25)
                self._draw_simple_sphere(0.032, 12, 6)

                # Solid sphere
                gl.glEnable(gl.GL_LIGHTING)
                gl.glColor3f(r_rgb[0]/255, r_rgb[1]/255, r_rgb[2]/255)
                self._draw_simple_sphere(0.016, 12, 8)
                gl.glDisable(gl.GL_LIGHTING)

                gl.glPopMatrix()

            # 9. Cache HUD data — drawn in paintEvent (after GL, via QPainter overlay)
            # IMPORTANT: do NOT call QPainter(self) from inside paintGL — it corrupts the GL context.
            self._hud_data = (sat_lat, sat_lon, alt_km, mc_id, mc_name, el, snr, pf, rtt, dop,
                              active, geo_trail)

        def _draw_simple_sphere(self, radius, slices, stacks):
            quad = glu.gluNewQuadric()
            glu.gluQuadricNormals(quad, glu.GLU_SMOOTH)
            glu.gluSphere(quad, radius, slices, stacks)
            glu.gluDeleteQuadric(quad)

        def _draw_footprint(self, lat_deg, lon_deg, alt_km):
            fp_pts = _footprint_circle(math.radians(lat_deg), math.radians(lon_deg), alt_km, n=72)
            gl.glBegin(gl.GL_LINE_LOOP)
            for lat_r, lon_r in fp_pts[:-1]:
                x, y, z = _ll2xyz(math.degrees(lat_r), math.degrees(lon_r), 1.001)
                gl.glVertex3f(x, y, z)
            gl.glEnd()

        def _draw_hud_qt(self, sat_lat, sat_lon, alt_km, mc_id, mc_name, el, snr, pf, rtt, dop, active, geo_trail):
            p = QtGui.QPainter(self)
            W, H = self.width(), self.height()

            # Top-right info box
            box_w, box_h = 210, 80
            bx, by = W - box_w - 8, 8
            p.fillRect(bx, by, box_w, box_h, QtGui.QColor(4, 8, 20, 210))
            p.setPen(QtGui.QPen(QtGui.QColor(0x22, 0x33, 0x55), 1))
            p.drawRect(bx, by, box_w, box_h)

            p.setFont(QtGui.QFont("Monospace", 10, QtGui.QFont.Bold))
            p.setPen(_qcolor(mc_id) if active else QtGui.QColor(0x44, 0x44, 0x55))
            p.drawText(bx + 7, by + 17, mc_name if active else "LOS — Not visible")

            p.setFont(QtGui.QFont("Monospace", 8))
            p.setPen(QtGui.QColor(0x88, 0x99, 0xAA))
            vy = by + 32
            for txt in [f"El {el:5.1f}°   SNR {snr:+5.1f} dB",
                        f"Pass {pf*100:5.1f}%  RTT {rtt:.1f} ms",
                        f"{sat_lat:.1f}°N  {sat_lon:.1f}°E"]:
                p.drawText(bx + 7, vy, txt)
                vy += 16

            # Top-left: altitude + footprint
            rho_deg = math.degrees(math.acos(R_EARTH_KM / (R_EARTH_KM + alt_km)))
            fp_km = int(2 * math.pi * R_EARTH_KM * rho_deg / 360.0)
            p.setFont(QtGui.QFont("Monospace", 8))
            p.setPen(QtGui.QColor(0x55, 0x77, 0x99))
            p.drawText(8, 18, f"Alt: {int(alt_km)} km   Footprint radius: {fp_km} km")

            # Bottom: pass timeline bar + legend
            bw = W - 24
            bar_y = H - 54
            p.fillRect(8, bar_y - 4, W - 16, 60, QtGui.QColor(3, 6, 16, 210))
            p.setPen(QtGui.QPen(QtGui.QColor(0x22, 0x33, 0x55), 1))
            p.drawLine(8, bar_y - 4, W - 8, bar_y - 4)

            with self._s._lock:
                switch_log = list(self._s._switch_log)
                start_mc   = self._s._pass_start_mc

            p.fillRect(12, bar_y, bw, 10, QtGui.QColor(0x0e, 0x0e, 0x1e))
            seg_pts = [(0.0, start_mc)] + [(f, m) for f, m in switch_log] + [(pf, mc_id)]
            for i in range(len(seg_pts) - 1):
                f0, mc0 = seg_pts[i]
                f1, _   = seg_pts[i+1]
                x0b = 12 + int(f0 * bw)
                x1b = 12 + int(f1 * bw)
                if x1b > x0b:
                    p.fillRect(x0b, bar_y, x1b - x0b, 10, _qcolor(mc0))
            cur_x = 12 + int(pf * bw)
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
            p.drawLine(cur_x, bar_y - 2, cur_x, bar_y + 12)

            col_dim = QtGui.QColor(0x55, 0x66, 0x77)
            p.setFont(QtGui.QFont("Monospace", 7))
            p.setPen(col_dim)
            for frac, lbl in ((0.0, "AOS"), (0.5, "TCA"), (1.0, "LOS")):
                tx = 12 + int(frac * bw)
                p.drawLine(tx, bar_y + 10, tx, bar_y + 14)
                p.drawText(tx - 8, bar_y + 23, lbl)

            lv = bar_y + 36
            lx = 12
            p.setFont(QtGui.QFont("Monospace", 8))
            for family, (r2, g2, b2) in _FAMILY_COLORS.items():
                if family == 'NONE':
                    continue
                p.fillRect(lx, lv - 9, 9, 9, QtGui.QColor(r2, g2, b2))
                p.setPen(col_dim)
                p.drawText(lx + 12, lv, family)
                lx += bw // 4

            p.end()

        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton:
                self._last_mouse = event.pos()

        def mouseMoveEvent(self, event):
            if self._last_mouse is not None and (event.buttons() & QtCore.Qt.LeftButton):
                dx = event.x() - self._last_mouse.x()
                dy = event.y() - self._last_mouse.y()
                self._rot_y += dx * 0.4
                self._rot_x += dy * 0.4
                self._rot_x = max(-89.0, min(89.0, self._rot_x))
                self._last_mouse = event.pos()
                self.update()

        def mouseReleaseEvent(self, event):
            self._last_mouse = None

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            self._zoom *= (0.9 if delta > 0 else 1.1)
            self._zoom = max(0.3, min(5.0, self._zoom))
            self.update()

        def paintEvent(self, event):
            # QOpenGLWidget: super().paintEvent() triggers paintGL internally,
            # which caches HUD data into self._hud_data.
            # We then draw the HUD overlay with QPainter — safe here, not inside paintGL.
            super().paintEvent(event)
            if self._hud_data is not None:
                self._draw_hud_qt(*self._hud_data)

else:
    class _GlobeWidget(QtWidgets.QWidget):
        def __init__(self, state, parent=None):
            super().__init__(parent)
            lbl = QtWidgets.QLabel("3D Globe requires PyOpenGL and Pillow.\npip3 install PyOpenGL Pillow")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("color: #667788; font-family: Monospace; background: #060a18;")
            QtWidgets.QVBoxLayout(self).addWidget(lbl)

        def update_globe(self): pass


# ── TAB 1: Link Analysis (matplotlib) ─────────────────────────────────────────

class _AnalyticsWidget(QtWidgets.QWidget):
    """
    Matplotlib dark-theme analytics panel with 3 subplots:
      - SNR vs Pass Fraction
      - MODCOD Distribution (horizontal bar)
      - Spectral Efficiency vs Pass
    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._s = state
        self._canvas = None
        self._fig = None
        self._ax_snr = None
        self._ax_mc = None
        self._ax_eff = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not _MPL_AVAILABLE:
            lbl = QtWidgets.QLabel("matplotlib not available.\nInstall with: pip3 install matplotlib")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("color: #667788; font-family: Monospace; font-size: 12pt;")
            layout.addWidget(lbl)
            return

        self._fig = Figure(figsize=(7, 8), dpi=96)
        self._fig.patch.set_facecolor('#060a18')

        gs_spec = gridspec.GridSpec(2, 2, figure=self._fig,
                                    hspace=0.42, wspace=0.35,
                                    left=0.10, right=0.95,
                                    top=0.93, bottom=0.07)
        self._ax_snr = self._fig.add_subplot(gs_spec[0, :])    # full top row
        self._ax_mc  = self._fig.add_subplot(gs_spec[1, 0])    # bottom-left
        self._ax_eff = self._fig.add_subplot(gs_spec[1, 1])    # bottom-right

        self._apply_dark_theme()

        self._canvas = FigureCanvas(self._fig)
        self._canvas.setStyleSheet("background-color: #060a18;")
        layout.addWidget(self._canvas)

    def _apply_dark_theme(self):
        for ax in (self._ax_snr, self._ax_mc, self._ax_eff):
            ax.set_facecolor('#0a0f20')
            for spine in ax.spines.values():
                spine.set_edgecolor('#1a2a44')
            ax.tick_params(colors='#667788', labelsize=8)
            ax.xaxis.label.set_color('#8899aa')
            ax.yaxis.label.set_color('#8899aa')
            ax.title.set_color('#aabbcc')

    def redraw(self):
        """Redraw all subplots. Called from slow timer on Qt main thread."""
        if not _MPL_AVAILABLE:
            return
        if self._fig is None:
            return

        # Read state under lock
        with self._s._lock:
            pass_hist = list(self._s._pass_history)
            history   = list(self._s._history)
            pf_cur    = self._s._pf
            snr_cur   = self._s._snr
            mc_cur    = self._s._mc_id
            active    = self._s._active

        # --- ax_snr: SNR vs Pass Fraction ---
        ax = self._ax_snr
        ax.cla()
        ax.set_facecolor('#0a0f20')
        for spine in ax.spines.values():
            spine.set_edgecolor('#1a2a44')
        ax.tick_params(colors='#667788', labelsize=8)
        ax.xaxis.label.set_color('#8899aa')
        ax.yaxis.label.set_color('#8899aa')
        ax.title.set_color('#aabbcc')

        if history:
            pf_vals  = [e['pf']  for e in history]
            snr_vals = [e['snr'] for e in history]
            mc_vals  = [e['mc_id'] for e in history]
            # Draw colored segments by MODCOD family
            for i in range(1, len(pf_vals)):
                fam = _modcod_family(mc_vals[i])
                col = _family_mpl_color(fam)
                ax.plot([pf_vals[i-1], pf_vals[i]], [snr_vals[i-1], snr_vals[i]],
                        color=col, linewidth=1.5, solid_capstyle='round')

        # Threshold lines
        thresholds = [
            ('QPSK',    1.0,   '#27ae60'),
            ('8PSK',    5.5,   '#2980b9'),
            ('16APSK',  9.0,   '#f39c12'),
            ('32APSK', 11.6,   '#e74c3c'),
        ]
        for fam_lbl, snr_thr, col_t in thresholds:
            ax.axhline(snr_thr, color=col_t, linewidth=0.8, linestyle='--', alpha=0.7)
            ax.text(1.01, snr_thr, fam_lbl, transform=ax.get_yaxis_transform(),
                    color=col_t, fontsize=6, va='center')

        # Current position
        if active:
            ax.axvline(pf_cur, color='#ffffff', linewidth=1.0, alpha=0.8)
            ax.plot(pf_cur, snr_cur, 'o', color='#ff4444', markersize=5, zorder=5)

        ax.set_xlim(0, 1)
        ax.set_xlabel("Pass Fraction", fontsize=8)
        ax.set_ylabel("SNR (dB)", fontsize=8)
        ax.set_title(f"SNR vs Pass Fraction   [Current: {snr_cur:+.1f} dB]", fontsize=9)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.set_xticklabels(['AOS', '25%', 'TCA', '75%', 'LOS'], fontsize=7, color='#667788')

        # --- ax_mc: MODCOD Distribution ---
        ax2 = self._ax_mc
        ax2.cla()
        ax2.set_facecolor('#0a0f20')
        for spine in ax2.spines.values():
            spine.set_edgecolor('#1a2a44')
        ax2.tick_params(colors='#667788', labelsize=7)
        ax2.xaxis.label.set_color('#8899aa')
        ax2.yaxis.label.set_color('#8899aa')
        ax2.title.set_color('#aabbcc')

        if pass_hist:
            counts = {'QPSK': 0, '8PSK': 0, '16APSK': 0, '32APSK': 0}
            total = len(pass_hist)
            for e in pass_hist:
                fam = _modcod_family(e['mc_id'])
                if fam in counts:
                    counts[fam] += 1
            families = list(counts.keys())
            percentages = [counts[f] / total * 100.0 for f in families]
            colors_bar = [_family_mpl_color(f) for f in families]
            y_pos = range(len(families))
            ax2.barh(list(y_pos), percentages, color=colors_bar, height=0.6, alpha=0.85)
            ax2.set_yticks(list(y_pos))
            ax2.set_yticklabels(families, fontsize=7, color='#8899aa')
        else:
            ax2.text(0.5, 0.5, 'No data', ha='center', va='center',
                     transform=ax2.transAxes, color='#445566', fontsize=9)

        ax2.set_xlim(0, 100)
        ax2.set_xlabel("% of Pass Time", fontsize=8)
        ax2.set_title("MODCOD Distribution", fontsize=9)

        # --- ax_eff: Spectral Efficiency vs Pass ---
        ax3 = self._ax_eff
        ax3.cla()
        ax3.set_facecolor('#0a0f20')
        for spine in ax3.spines.values():
            spine.set_edgecolor('#1a2a44')
        ax3.tick_params(colors='#667788', labelsize=7)
        ax3.xaxis.label.set_color('#8899aa')
        ax3.yaxis.label.set_color('#8899aa')
        ax3.title.set_color('#aabbcc')

        fixed_eff = _MODCOD_BITS_PER_SYMBOL.get(4, 0.989)   # QPSK 1/2

        if history:
            pf_e  = [e['pf']  for e in history]
            eff_e = [_MODCOD_BITS_PER_SYMBOL.get(e['mc_id'], fixed_eff) for e in history]

            fam_eff = [_modcod_family(e['mc_id']) for e in history]
            for i in range(1, len(pf_e)):
                col = _family_mpl_color(fam_eff[i])
                ax3.plot([pf_e[i-1], pf_e[i]], [eff_e[i-1], eff_e[i]],
                         color=col, linewidth=1.5, solid_capstyle='round')

            fixed_line = [fixed_eff] * len(pf_e)
            ax3.plot(pf_e, fixed_line, '--', color='#445566', linewidth=1.0,
                     label='Fixed QPSK 1/2', alpha=0.8)

            # Fill between
            try:
                ax3.fill_between(pf_e, fixed_line, eff_e,
                                 where=[e >= fixed_eff for e in eff_e],
                                 color='#27ae60', alpha=0.15)
            except Exception:
                pass

            avg_gain = (sum(eff_e) / len(eff_e)) - fixed_eff
            ax3.set_title(f"Spectral Efficiency  ACM Gain: +{avg_gain:.2f} b/sym avg", fontsize=8)
            ax3.legend(fontsize=6, loc='lower right',
                       facecolor='#0a0f20', edgecolor='#1a2a44',
                       labelcolor='#667788')
        else:
            ax3.set_title("Spectral Efficiency vs Pass", fontsize=9)
            ax3.text(0.5, 0.5, 'No data', ha='center', va='center',
                     transform=ax3.transAxes, color='#445566', fontsize=9)

        ax3.set_xlim(0, 1)
        ax3.set_ylim(0, 6.5)
        ax3.set_xlabel("Pass Fraction", fontsize=8)
        ax3.set_ylabel("bits / symbol", fontsize=8)
        ax3.set_xticks([0, 0.5, 1.0])
        ax3.set_xticklabels(['AOS', 'TCA', 'LOS'], fontsize=7, color='#667788')

        self._canvas.draw_idle()


# ── TAB 2: Sky View (polar sky plot) ─────────────────────────────────────────

class _SkyPlotWidget(QtWidgets.QWidget):
    """Polar sky plot — satellite trail coloured by MODCOD."""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._s = state

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        W, H = self.width(), self.height()
        plot_d = min(W - 20, int(H * 0.60))
        R      = max(10, plot_d // 2 - 10)
        cx     = W // 2
        cy     = 20 + R + 10

        self._draw_grid(p, cx, cy, R)
        self._draw_predicted_arc(p, cx, cy, R)
        self._draw_trail(p, cx, cy, R)
        self._draw_satellite(p, cx, cy, R)
        self._draw_status(p, W, H, cy + R + 18)
        p.end()

    @staticmethod
    def _sky_xy(az_deg, el_deg, cx, cy, R):
        el_deg = max(0.0, min(90.0, el_deg))
        r  = R * (1.0 - el_deg / 90.0)
        ar = math.radians(az_deg)
        return int(cx + r * math.sin(ar)), int(cy - r * math.cos(ar))

    def _draw_grid(self, p, cx, cy, R):
        p.setPen(QtGui.QPen(QtGui.QColor(0x33, 0x33, 0x55), 1))
        p.setBrush(QtGui.QBrush(QtGui.QColor(0x06, 0x08, 0x18)))
        p.drawEllipse(cx - R, cy - R, 2*R, 2*R)

        dot_pen = QtGui.QPen(QtGui.QColor(0x22, 0x33, 0x55), 1, QtCore.Qt.DotLine)
        p.setPen(dot_pen)
        p.setBrush(QtCore.Qt.NoBrush)
        for el_r in (30, 60):
            r = int(R * (1.0 - el_r / 90.0))
            p.drawEllipse(cx - r, cy - r, 2*r, 2*r)
        p.drawLine(cx, cy - R, cx, cy + R)
        p.drawLine(cx - R, cy, cx + R, cy)

        p.setFont(QtGui.QFont("Monospace", 9, QtGui.QFont.Bold))
        p.setPen(QtGui.QColor(0x88, 0x99, 0xAA))
        for text, dx, dy in [("N", -4, -R-4), ("S", -4, R+14),
                               ("E", R+6,  4), ("W", -R-16, 4)]:
            p.drawText(cx + dx, cy + dy, text)

        p.setFont(QtGui.QFont("Monospace", 7))
        p.setPen(QtGui.QColor(0x33, 0x44, 0x55))
        for el_r, lbl in ((30, "30°"), (60, "60°")):
            r = int(R * (1.0 - el_r / 90.0))
            p.drawText(cx + r + 3, cy - 3, lbl)
        p.drawText(cx + 3, cy - 3, "90°")

    def _draw_predicted_arc(self, p, cx, cy, R):
        p.setPen(QtGui.QPen(QtGui.QColor(0x28, 0x28, 0x44), 1,
                            QtCore.Qt.DashLine))
        with self._s._lock:
            arc = list(self._s._arc_sky)
        pts = [self._sky_xy(az, el, cx, cy, R) for az, el in arc]
        for i in range(len(pts) - 1):
            p.drawLine(*pts[i], *pts[i+1])

    def _draw_trail(self, p, cx, cy, R):
        with self._s._lock:
            trail = list(self._s._trail)
        if len(trail) < 2:
            return
        for i in range(1, len(trail)):
            az0, el0, mc0, _ = trail[i-1]
            az1, el1, mc1, _ = trail[i]
            x0, y0 = self._sky_xy(az0, el0, cx, cy, R)
            x1, y1 = self._sky_xy(az1, el1, cx, cy, R)
            p.setPen(QtGui.QPen(_qcolor(mc1), 2))
            p.drawLine(x0, y0, x1, y1)

    def _draw_satellite(self, p, cx, cy, R):
        with self._s._lock:
            el, az, mc, active = (self._s._el, self._s._az,
                                   self._s._mc_id, self._s._active)
        if not active:
            return
        x, y = self._sky_xy(az, el, cx, cy, R)
        col = _qcolor(mc)
        glow = QtGui.QColor(col)
        glow.setAlpha(50)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(x - 12, y - 12, 24, 24)
        p.setBrush(col)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1))
        p.drawEllipse(x - 7, y - 7, 14, 14)
        p.setPen(QtGui.QColor(255, 255, 255))
        p.setFont(QtGui.QFont("Monospace", 8))
        p.drawText(x + 10, y - 4, f"{el:.1f}°")

    def _draw_status(self, p, W, H, y0):
        with self._s._lock:
            el, snr, mc, name = (self._s._el, self._s._snr,
                                  self._s._mc_id, self._s._mc_name)
            pf, rtt, rain, dop = (self._s._pf, self._s._rtt,
                                   self._s._rain, self._s._dop)
            active   = self._s._active
            swlog    = list(self._s._switch_log)
            start_mc = self._s._pass_start_mc

        ph = H - y0 - 6
        if ph < 10:
            return
        p.fillRect(8, y0, W - 16, ph, QtGui.QColor(0x05, 0x08, 0x14))
        p.setPen(QtGui.QPen(QtGui.QColor(0x22, 0x33, 0x55), 1))
        p.drawRect(8, y0, W - 16, ph)

        col_bright = QtGui.QColor(0xDD, 0xEE, 0xFF)
        col_dim    = QtGui.QColor(0x77, 0x88, 0x99)
        col_mc     = _qcolor(mc)

        p.setFont(QtGui.QFont("Monospace", 11, QtGui.QFont.Bold))
        p.setPen(col_mc if active else QtGui.QColor(0x44, 0x44, 0x55))
        headline = f"● {name}" if active else "● SATELLITE NOT IN VIEW"
        p.drawText(18, y0 + 20, headline)
        if not active:
            return

        lx1, lx2 = 18, W // 2
        vy = y0 + 38
        fields = [
            ("Elevation", f"{el:6.1f}°",      "RTT",     f"{rtt:5.1f} ms"),
            ("SNR",       f"{snr:+6.1f} dB",   "Rain",    f"{rain:.2f} dB"),
            ("Pass",      f"{pf*100:5.1f}%",    "Doppler", f"{dop/1e3:+7.1f} kHz"),
        ]
        p.setFont(QtGui.QFont("Monospace", 9))
        for lbl1, val1, lbl2, val2 in fields:
            p.setPen(col_dim)
            p.drawText(lx1, vy, lbl1 + ":")
            p.drawText(lx2, vy, lbl2 + ":")
            p.setPen(col_bright)
            p.drawText(lx1 + 78, vy, val1)
            p.drawText(lx2 + 78, vy, val2)
            vy += 18

        # MODCOD history bar
        vy += 6
        p.setFont(QtGui.QFont("Monospace", 8))
        p.setPen(col_dim)
        p.drawText(lx1, vy, "MODCOD history this pass:")
        vy += 13
        bw = W - 32
        bh = 12
        p.fillRect(lx1, vy, bw, bh, QtGui.QColor(0x10, 0x10, 0x20))
        seg_pts = [(0.0, start_mc)] + [(f, m) for f, m in swlog] + [(pf, mc)]
        for i in range(len(seg_pts) - 1):
            f0, mc0 = seg_pts[i]
            f1, _   = seg_pts[i + 1]
            x0 = lx1 + int(f0 * bw)
            x1 = lx1 + int(f1 * bw)
            if x1 > x0:
                p.fillRect(x0, vy, x1 - x0, bh, _qcolor(mc0))
        cur = lx1 + int(pf * bw)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
        p.drawLine(cur, vy - 2, cur, vy + bh + 2)
        p.setFont(QtGui.QFont("Monospace", 7))
        p.setPen(col_dim)
        for frac, lbl in ((0.0, "AOS"), (0.5, "TCA"), (1.0, "LOS")):
            tx = lx1 + int(frac * bw)
            p.drawLine(tx, vy + bh, tx, vy + bh + 4)
            p.drawText(tx - 8, vy + bh + 13, lbl)

        # Legend
        vy += bh + 22
        lx = lx1
        p.setFont(QtGui.QFont("Monospace", 8))
        for family, (r, g, b) in _FAMILY_COLORS.items():
            if family == 'NONE':
                continue
            p.fillRect(lx, vy - 9, 10, 10, QtGui.QColor(r, g, b))
            p.setPen(col_dim)
            p.drawText(lx + 13, vy, family)
            lx += (W - 32) // 4


# ── Main window: three tabs + shared state ────────────────────────────────────

class _OrbVizWindow(QtWidgets.QWidget):
    """
    Main visualizer window.

    Holds all shared state (lock-protected), exposes push_state() for the GR
    message thread, and owns the QTimers that drive periodic redraws.
    """

    _sig_update = QtCore.pyqtSignal()   # fired from GR thread → Qt main thread

    def __init__(self, title: str,
                 gs_lat_deg: float = 0.0,
                 gs_lon_deg: float = 0.0,
                 alt_km: float = 500.0,
                 pass_inclination_deg: float = 0.0):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(760, 860)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.setStyleSheet("background-color: #060a18;")

        # ── Shared state ──────────────────────────────────────────────────────
        self._lock = threading.Lock()
        self._gs_lat = math.radians(gs_lat_deg)
        self._gs_lon = math.radians(gs_lon_deg)
        self._alt_km = alt_km
        self._pass_incl_deg = pass_inclination_deg

        # Live channel state
        self._el      = 5.0
        self._az      = 180.0
        self._snr     = -10.0
        self._mc_id   = 1
        self._mc_name = "QPSK 1/4"
        self._pf      = 0.0
        self._rtt     = 13.0
        self._rain    = 0.0
        self._dop     = 0.0
        self._fspl    = 164.5
        self._scint   = 0.0
        self._rician  = 0.0
        self._active  = False

        # Sky trail: 4-tuple (az_deg, el_deg, mc_id, pass_frac)
        self._trail     = []
        self._MAX_TRAIL = 600

        # Geo trail: (lat_deg, lon_deg, mc_id)
        self._geo_trail = []

        # MODCOD switch log: [(pass_frac, mc_id)]
        self._switch_log   = []
        self._pass_start_mc = 1

        # History for analytics: list of dicts, max 1800
        self._history     = []
        self._MAX_HISTORY = 1800

        # Current pass history only (reset on new pass)
        self._pass_history = []

        # Pre-computed static arcs
        self._arc_sky = self._build_arc_sky()
        self._arc_geo = self._build_arc_geo()

        # Subsatellite lat/lon (for Earth View center)
        self._sat_lat_r = 0.0
        self._sat_lon_r = 0.0

        # ── Layout ────────────────────────────────────────────────────────────
        self._globe_widget  = _GlobeWidget(self)
        self._analytics_widget = _AnalyticsWidget(self)
        self._sky_widget    = _SkyPlotWidget(self)

        self._tabs = QtWidgets.QTabWidget()
        self._tabs.addTab(self._globe_widget,      "🌐 3D Globe")
        self._tabs.addTab(self._analytics_widget,  "📊 Link Analysis")
        self._tabs.addTab(self._sky_widget,        "📡 Sky View")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

        # ── Signals & timers ──────────────────────────────────────────────────
        self._sig_update.connect(self._refresh_fast, QtCore.Qt.QueuedConnection)

        # Fast timer: 100ms → Earth View + Sky View
        self._fast_timer = QtCore.QTimer(self)
        self._fast_timer.setInterval(100)
        self._fast_timer.timeout.connect(self._refresh_fast)
        self._fast_timer.start()

        # Slow timer: 800ms → Analytics (only when visible)
        self._slow_timer = QtCore.QTimer(self)
        self._slow_timer.setInterval(800)
        self._slow_timer.timeout.connect(self._refresh_slow)
        self._slow_timer.start()

    # ── Arc pre-computation ───────────────────────────────────────────────────

    def _build_arc_sky(self):
        pts = []
        for frac in np.linspace(0, 1, 200):
            az = (180.0 * (1.0 - frac) + self._pass_incl_deg) % 360.0
            el = 90.0 * math.sin(math.pi * frac)
            pts.append((az, el))
        return pts

    def _build_arc_geo(self):
        pts = []
        for frac in np.linspace(0, 1, 200):
            az = (180.0 * (1.0 - frac) + self._pass_incl_deg) % 360.0
            el = max(5.0, 90.0 * math.sin(math.pi * frac))
            try:
                lat_r, lon_r = _subsatellite_point(
                    self._gs_lat, self._gs_lon,
                    math.radians(az), math.radians(el), self._alt_km)
                pts.append((math.degrees(lat_r), math.degrees(lon_r)))
            except Exception:
                pass
        return pts

    # ── Thread-safe state update (called from GR message thread) ─────────────

    def push_state(self, el, az, snr, mc_id, mc_name, pf, rtt, rain, dop, fspl, scint, rician):
        with self._lock:
            prev_mc = self._mc_id
            self._el      = el
            self._az      = az
            self._snr     = snr
            self._mc_id   = mc_id
            self._mc_name = mc_name
            self._pf      = pf
            self._rtt     = rtt
            self._rain    = rain
            self._dop     = dop
            self._fspl    = fspl
            self._scint   = scint
            self._rician  = rician
            self._active  = (el >= 5.0)

            if self._active:
                # New-pass detection: trail[3] is pass_frac (4-tuple fix)
                if self._trail and pf < 0.05 and self._trail[-1][3] > 0.9:
                    self._trail.clear()
                    self._geo_trail.clear()
                    self._switch_log.clear()
                    self._pass_history.clear()
                    self._pass_start_mc = mc_id

                # Sky trail (4-tuple)
                self._trail.append((az, el, mc_id, pf))
                if len(self._trail) > self._MAX_TRAIL:
                    self._trail.pop(0)

                # Geo trail
                try:
                    lat_r, lon_r = _subsatellite_point(
                        self._gs_lat, self._gs_lon,
                        math.radians(az), math.radians(max(5.0, el)),
                        self._alt_km)
                    self._sat_lat_r = lat_r
                    self._sat_lon_r = lon_r
                    self._geo_trail.append(
                        (math.degrees(lat_r), math.degrees(lon_r), mc_id))
                    if len(self._geo_trail) > self._MAX_TRAIL:
                        self._geo_trail.pop(0)
                except Exception:
                    pass

                if mc_id != prev_mc:
                    self._switch_log.append((pf, mc_id))
                    if len(self._switch_log) > 40:
                        self._switch_log.pop(0)

                # History snapshot
                entry = dict(pf=pf, snr=snr, el=el, mc_id=mc_id,
                             rain=rain, fspl=fspl, scint=scint, rician=rician)
                self._history.append(entry)
                if len(self._history) > self._MAX_HISTORY:
                    self._history.pop(0)
                self._pass_history.append(entry)

        self._sig_update.emit()

    # ── Qt slots (main thread) ────────────────────────────────────────────────

    def _refresh_fast(self):
        """Fast refresh: update Earth View (tab 0) and Sky View (tab 2)."""
        self._globe_widget.update()
        self._sky_widget.update()

    def _refresh_slow(self):
        """Slow refresh: redraw Link Analysis (tab 1) only when visible."""
        if self._tabs.currentIndex() == 1:
            self._analytics_widget.redraw()


# ── GNU Radio block ────────────────────────────────────────────────────────────

class orbit_visualizer(gr.basic_block):
    """
    DVB-S2 ACM Orbit Visualizer (message-only block).

    Opens a floating three-tab window:
      Tab 0 – Earth View    : orthographic satellite eye-view projection
      Tab 1 – Link Analysis : matplotlib analytics (SNR, MODCOD dist, ACM gain)
      Tab 2 – Sky View      : polar sky plot with MODCOD-coloured trail

    Message ports:
      channel_state_in  ← dvbs2acm_leo_channel.channel_state
      modcod_in         ← acm_controller.modcod_out  (optional but recommended)

    Parameters:
      title                : Window title string
      pass_inclination_deg : Rotates the satellite arc azimuth (0 = S→N vertical)
      gs_lat_deg           : Ground station latitude  (default 0°N)
      gs_lon_deg           : Ground station longitude (default 0°E)
      altitude_km          : Orbital altitude for footprint calculation (default 500)
    """

    def __init__(self,
                 title: str                  = "DVB-S2 ACM — LEO Pass Visualizer",
                 pass_inclination_deg: float = 0.0,
                 gs_lat_deg: float           = 0.0,
                 gs_lon_deg: float           = 0.0,
                 altitude_km: float          = 500.0):
        gr.basic_block.__init__(self,
            name="dvbs2acm_orbit_visualizer",
            in_sig=[],
            out_sig=[])

        self._title            = title
        self._pass_inclination = float(pass_inclination_deg)
        self._gs_lat           = float(gs_lat_deg)
        self._gs_lon           = float(gs_lon_deg)
        self._altitude_km      = float(altitude_km)

        self._lock             = threading.Lock()
        self._current_mc_id    = 1
        self._current_mc_name  = "QPSK 1/4"
        self._window           = None

        self.message_port_register_in(pmt.intern("channel_state_in"))
        self.set_msg_handler(pmt.intern("channel_state_in"),
                             self._handle_channel_state)

        self.message_port_register_in(pmt.intern("modcod_in"))
        self.set_msg_handler(pmt.intern("modcod_in"), self._handle_modcod)

        if _QT_AVAILABLE:
            self._try_create_window()

    def _try_create_window(self):
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                self._window = _OrbVizWindow(
                    title                = self._title,
                    gs_lat_deg           = self._gs_lat,
                    gs_lon_deg           = self._gs_lon,
                    alt_km               = self._altitude_km,
                    pass_inclination_deg = self._pass_inclination,
                )
                self._window.show()
        except Exception as e:
            print(f"[OrbitViz] Window init failed: {e}", flush=True)

    def _ensure_window(self):
        if self._window is None and _QT_AVAILABLE:
            self._try_create_window()

    # ── Message handlers (GR scheduler threads) ───────────────────────────────

    def _handle_modcod(self, msg):
        try:
            if not pmt.is_dict(msg):
                return
            mid_pmt  = pmt.dict_ref(msg, pmt.intern("modcod_id"),   pmt.PMT_NIL)
            name_pmt = pmt.dict_ref(msg, pmt.intern("modcod_name"), pmt.PMT_NIL)
            with self._lock:
                if not pmt.equal(mid_pmt, pmt.PMT_NIL):
                    self._current_mc_id = int(pmt.to_python(mid_pmt))
                if not pmt.equal(name_pmt, pmt.PMT_NIL):
                    self._current_mc_name = str(pmt.to_python(name_pmt))
        except Exception:
            pass

    def _handle_channel_state(self, msg):
        try:
            if not pmt.is_dict(msg):
                return

            def _get(key, default=0.0):
                v = pmt.dict_ref(msg, pmt.intern(key), pmt.PMT_NIL)
                return default if pmt.equal(v, pmt.PMT_NIL) else float(pmt.to_python(v))

            el     = _get("elevation_deg")
            snr    = _get("snr_db")
            pf     = _get("pass_fraction")
            rtt    = _get("rtt_ms")
            rain   = _get("rain_db")
            dop    = _get("doppler_hz")
            fspl   = _get("fspl_db",   164.5)
            scint  = _get("scint_db",  0.0)
            rician = _get("rician_db", 0.0)

            # Synthesize azimuth from pass_fraction + inclination offset
            az = (180.0 * (1.0 - pf) + self._pass_inclination) % 360.0

            with self._lock:
                mc_id   = self._current_mc_id
                mc_name = self._current_mc_name

            self._ensure_window()
            if self._window is not None:
                self._window.push_state(
                    el, az, snr, mc_id, mc_name, pf,
                    rtt, rain, dop, fspl, scint, rician)
        except Exception:
            pass
