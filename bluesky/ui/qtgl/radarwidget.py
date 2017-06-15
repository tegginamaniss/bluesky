from os import path
try:
    from PyQt5.QtCore import Qt, qCritical, QTimer, pyqtSlot
    from PyQt5.QtOpenGL import QGLWidget
    QT_VERSION = 5
except ImportError:
    from PyQt4.QtCore import Qt, qCritical, QTimer, pyqtSlot
    from PyQt4.QtOpenGL import QGLWidget
    QT_VERSION = 4

import numpy as np
import OpenGL.GL as gl
from ctypes import c_float, c_int, Structure

# Local imports
import bluesky as bs
from bluesky import settings
from bluesky.tools.aero import ft, nm, kts
from bluesky.sim.qtgl import PanZoomEvent, PanZoomEventType, MainManager as manager
from bluesky.navdb import load_aptsurface, load_coastlines
from .glhelpers import BlueSkyProgram, RenderObject, Font, UniformBuffer, \
    update_buffer, create_empty_buffer

# Register settings defaults
settings.set_variable_defaults(gfx_path='data/graphics', text_size=13, apt_size=10, wpt_size=10, ac_size=16)

# Static defines
MAX_NAIRCRAFT         = 10000
MAX_NCONFLICTS        = 25000
MAX_ROUTE_LENGTH      = 100
MAX_POLYPREV_SEGMENTS = 100
MAX_ALLPOLYS_SEGMENTS = 2000
MAX_CUST_WPT          = 1000
MAX_TRAILLEN          = MAX_NAIRCRAFT * 1000

REARTH_INV            = 1.56961231e-7

# Colors
red                   = (255, 0,   0)
green                 = (0,   255, 0)
blue                  = (0,   0,   255)
lightblue             = (0,   204, 255)
lightblue2            = (85,  85,  115)
lightblue3            = (148, 178, 235)
lightblue4            = (220, 250, 255)
cyan                  = (0,   255, 255)
amber                 = (255, 160, 0)
magenta               = (255, 0,   255)
grey                  = (100, 100, 100)
white                 = (255, 255, 255)
lightgrey             = (160, 160, 160)

VERTEX_IS_LATLON, VERTEX_IS_METERS, VERTEX_IS_SCREEN = range(3)
ATTRIB_VERTEX, ATTRIB_TEXCOORDS, ATTRIB_LAT, ATTRIB_LON, ATTRIB_ORIENTATION, ATTRIB_COLOR, ATTRIB_TEXDEPTH = range(7)
ATTRIB_SELSSD, ATTRIB_LAT0, ATTRIB_LON0, ATTRIB_ALT0, ATTRIB_TAS0, ATTRIB_TRK0, ATTRIB_LAT1, ATTRIB_LON1, ATTRIB_ALT1, ATTRIB_TAS1, ATTRIB_TRK1 = range(11)


class nodeData(object):
    def __init__(self):
        self.polynames = dict()
        self.polydata  = np.array([], dtype=np.float32)
        self.custwplbl = ''
        self.custwplat = np.array([], dtype=np.float32)
        self.custwplon = np.array([], dtype=np.float32)

        # Filteralt settings
        self.filteralt = False

        # Create trail data
        self.traillat0 = []
        self.traillon0 = []
        self.traillat1 = []
        self.traillon1 = []


class radarUBO(UniformBuffer):
    class Data(Structure):
        _fields_ = [("wrapdir", c_int), ("wraplon", c_float), ("panlat", c_float), ("panlon", c_float),
        ("zoom", c_float), ("screen_width", c_int), ("screen_height", c_int), ("vertex_scale_type", c_int)]

    data = Data()

    def __init__(self):
        super(radarUBO, self).__init__(self.data)

    def set_wrap(self, wraplon, wrapdir):
        self.data.wrapdir = wrapdir
        self.data.wraplon = wraplon

    def set_pan_and_zoom(self, panlat, panlon, zoom):
        self.data.panlat = panlat
        self.data.panlon = panlon
        self.data.zoom   = zoom

    def set_win_width_height(self, w, h):
        self.data.screen_width  = w
        self.data.screen_height = h

    def enable_wrap(self, flag=True):
        if not flag:
            wrapdir = self.data.wrapdir
            self.data.wrapdir = 0
            self.update(0, 4)
            self.data.wrapdir = wrapdir
        else:
            self.update(0, 4)

    def set_vertex_scale_type(self, vertex_scale_type):
        self.data.vertex_scale_type = vertex_scale_type
        self.update()


class RadarWidget(QGLWidget):
    vcount_circle = 36
    width = height = 600
    viewport = (0, 0, width, height)
    panlat = 0.0
    panlon = 0.0
    zoom = 1.0
    ar = 1.0
    flat_earth = 1.0
    wraplon = int(-999)
    wrapdir = int(0)
    max_texture_size = 0

    do_text = True
    invalid_count = 0

    def __init__(self, shareWidget=None):
        super(RadarWidget, self).__init__(shareWidget=shareWidget)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.PanGesture)
        self.grabGesture(Qt.PinchGesture)
        # self.grabGesture(Qt.SwipeGesture)

        # The number of aircraft in the simulation
        self.map_texture    = 0
        self.naircraft      = 0
        self.nwaypoints     = 0
        self.ncustwpts      = 0
        self.nairports      = 0
        self.route_acid     = ""
        self.ssd_ownship    = set()
        self.apt_inrange    = np.array([])
        self.ssd_all        = False
        self.ssd_conflicts   = False
        self.iactconn       = 0
        self.nodedata       = list()

        # Display flags
        self.show_map       = True
        self.show_coast     = True
        self.show_traf      = True
        self.show_pz        = False
        self.show_lbl       = True
        self.show_wpt       = 1
        self.show_apt       = 1

        self.initialized    = False

        # Connect to manager's nodelist changed and activenode changed signal
        manager.instance.nodes_changed.connect(self.nodesChanged)
        manager.instance.activenode_changed.connect(self.actnodeChanged)

        # Load vertex data
        self.vbuf_asphalt, self.vbuf_concrete, self.vbuf_runways, self.vbuf_rwythr, \
            self.apt_ctrlat, self.apt_ctrlon, self.apt_indices, rwythr = load_aptsurface()

    @pyqtSlot(str, tuple, int)
    def nodesChanged(self, address, nodeid, connidx):
        # For each node we have to keep data such as the visible polygons, etc.
        self.nodedata.append(nodeData())

    @pyqtSlot(tuple, int)
    def actnodeChanged(self, nodeid, connidx):
        self.iactconn = connidx
        nact = self.nodedata[connidx]
        self.makeCurrent()

        # Polygon data change after node change
        if len(nact.polydata) > 0:
            update_buffer(self.allpolysbuf, nact.polydata)

        self.allpolys.set_vertex_count(len(nact.polydata) / 2)

        # Update trail buffer after node change
        update_buffer(self.trailbuf, np.array(
              zip(nact.traillat0, nact.traillon0,
                  nact.traillat1, nact.traillon1), dtype=np.float32))

        self.traillines.set_vertex_count(4 * len(nact.traillat0))

    def create_objects(self):
        if not self.isValid():
            self.invalid_count += 1
            print 'Radarwidget: Context not valid in create_objects, count=%d' % self.invalid_count
            QTimer.singleShot(100, self.create_objects)
            return

        # Make the radarwidget context current, necessary when create_objects is not called from initializeGL
        self.makeCurrent()

        text_size = settings.text_size
        apt_size  = settings.apt_size
        wpt_size  = settings.wpt_size
        ac_size   = settings.ac_size

        # Initialize font for radar view with specified settings
        self.font = Font()
        self.font.create_font_array()
        self.font.init_shader(self.text_shader)

        # Load and bind world texture
        max_texture_size = gl.glGetIntegerv(gl.GL_MAX_TEXTURE_SIZE)
        print 'Maximum supported texture size: %d' % max_texture_size
        for i in [16384, 8192, 4096]:
            if max_texture_size >= i:
                fname = path.join(settings.gfx_path, 'world.%dx%d.dds' % (i, i / 2))
                print 'Loading texture ' + fname
                self.map_texture = self.bindTexture(fname)
                break

        # Create initial empty buffers for aircraft position, orientation, label, and color
        # usage flag indicates drawing priority:
        #
        # gl.GL_STREAM_DRAW  =  most frequent update
        # gl.GL_DYNAMIC_DRAW =  update
        # gl.GL_STATIC_DRAW  =  less frequent update

        self.achdgbuf      = create_empty_buffer(MAX_NAIRCRAFT * 4, usage=gl.GL_STREAM_DRAW)
        self.aclatbuf      = create_empty_buffer(MAX_NAIRCRAFT * 4, usage=gl.GL_STREAM_DRAW)
        self.aclonbuf      = create_empty_buffer(MAX_NAIRCRAFT * 4, usage=gl.GL_STREAM_DRAW)
        self.acaltbuf      = create_empty_buffer(MAX_NAIRCRAFT * 4, usage=gl.GL_STREAM_DRAW)
        self.actasbuf      = create_empty_buffer(MAX_NAIRCRAFT * 4, usage=gl.GL_STREAM_DRAW)
        self.accolorbuf    = create_empty_buffer(MAX_NAIRCRAFT * 4, usage=gl.GL_STREAM_DRAW)
        self.aclblbuf      = create_empty_buffer(MAX_NAIRCRAFT * 24, usage=gl.GL_STREAM_DRAW)
        self.confcpabuf    = create_empty_buffer(MAX_NCONFLICTS * 16, usage=gl.GL_STREAM_DRAW)
        self.trailbuf      = create_empty_buffer(MAX_TRAILLEN * 16, usage=gl.GL_STREAM_DRAW)

        self.polyprevbuf   = create_empty_buffer(MAX_POLYPREV_SEGMENTS * 8, usage=gl.GL_DYNAMIC_DRAW)
        self.allpolysbuf   = create_empty_buffer(MAX_ALLPOLYS_SEGMENTS * 16, usage=gl.GL_DYNAMIC_DRAW)
        self.routebuf      = create_empty_buffer(MAX_ROUTE_LENGTH * 8, usage=gl.GL_DYNAMIC_DRAW)
        self.routewplatbuf = create_empty_buffer(MAX_ROUTE_LENGTH * 4, usage=gl.GL_DYNAMIC_DRAW)
        self.routewplonbuf = create_empty_buffer(MAX_ROUTE_LENGTH * 4, usage=gl.GL_DYNAMIC_DRAW)
        self.routelblbuf   = create_empty_buffer(MAX_ROUTE_LENGTH * 20, usage=gl.GL_DYNAMIC_DRAW)

        self.custwplatbuf  = create_empty_buffer(MAX_CUST_WPT * 4, usage=gl.GL_STATIC_DRAW)
        self.custwplonbuf  = create_empty_buffer(MAX_CUST_WPT * 4, usage=gl.GL_STATIC_DRAW)
        self.custwplblbuf  = create_empty_buffer(MAX_CUST_WPT * 5, usage=gl.GL_STATIC_DRAW)

        # ------- Map ------------------------------------
        mapvertices = np.array([(-90.0, 540.0), (-90.0, -540.0), (90.0, -540.0), (90.0, 540.0)], dtype=np.float32)
        texcoords   = np.array([(1, 3), (1, 0), (0, 0), (0, 3)], dtype=np.float32)
        self.map    = RenderObject(gl.GL_TRIANGLE_FAN, vertex=mapvertices, texcoords=texcoords)

        # ------- Coastlines -----------------------------
        coastvertices, coastindices = load_coastlines()
        self.coastlines   = RenderObject(gl.GL_LINES, vertex=coastvertices, color=lightblue2)
        self.vcount_coast = len(coastvertices)
        self.coastindices = coastindices
        del coastvertices

        # ------- Airport graphics -----------------------
        self.runways    = RenderObject(gl.GL_TRIANGLES, vertex=self.vbuf_runways, color=grey)
        self.thresholds = RenderObject(gl.GL_TRIANGLES, vertex=self.vbuf_rwythr, color=white)
        self.taxiways   = RenderObject(gl.GL_TRIANGLES, vertex=self.vbuf_asphalt, color=grey)
        self.pavement   = RenderObject(gl.GL_TRIANGLES, vertex=self.vbuf_concrete, color=lightgrey)

        # Polygon preview object
        self.polyprev = RenderObject(gl.GL_LINE_LOOP, vertex=self.polyprevbuf, color=lightblue)

        # Fixed polygons
        self.allpolys = RenderObject(gl.GL_LINES, vertex=self.allpolysbuf, color=blue)

        # ------- SSD object -----------------------------
        self.ssd = RenderObject(gl.GL_POINTS)
        self.ssd.selssdbuf = self.ssd.bind_attrib(ATTRIB_SELSSD, 1, np.zeros(MAX_NAIRCRAFT, dtype=np.uint8), datatype=gl.GL_UNSIGNED_BYTE, instance_divisor=1)
        self.ssd.bind_attrib(ATTRIB_LAT0, 1, self.aclatbuf, instance_divisor=1)
        self.ssd.bind_attrib(ATTRIB_LON0, 1, self.aclonbuf, instance_divisor=1)
        self.ssd.bind_attrib(ATTRIB_ALT0, 1, self.acaltbuf, instance_divisor=1)
        self.ssd.bind_attrib(ATTRIB_TAS0, 1, self.actasbuf, instance_divisor=1)
        self.ssd.bind_attrib(ATTRIB_TRK0, 1, self.achdgbuf, instance_divisor=1)
        self.ssd.bind_attrib(ATTRIB_LAT1, 1, self.aclatbuf)
        self.ssd.bind_attrib(ATTRIB_LON1, 1, self.aclonbuf)
        self.ssd.bind_attrib(ATTRIB_ALT1, 1, self.acaltbuf)
        self.ssd.bind_attrib(ATTRIB_TAS1, 1, self.actasbuf)
        self.ssd.bind_attrib(ATTRIB_TRK1, 1, self.achdgbuf)

        # ------- Protected Zone -------------------------
        circlevertices = np.transpose(np.array((2.5 * nm * np.cos(np.linspace(0.0, 2.0 * np.pi, self.vcount_circle)), 2.5 * nm * np.sin(np.linspace(0.0, 2.0 * np.pi, self.vcount_circle))), dtype=np.float32))
        self.protectedzone = RenderObject(gl.GL_LINE_LOOP, vertex=circlevertices)
        self.protectedzone.bind_attrib(ATTRIB_LAT, 1, self.aclatbuf, instance_divisor=1)
        self.protectedzone.bind_attrib(ATTRIB_LON, 1, self.aclonbuf, instance_divisor=1)
        self.protectedzone.bind_color(self.accolorbuf, instance_divisor=1)

        # ------- A/C symbol -----------------------------
        acvertices = np.array([(0.0, 0.5 * ac_size), (-0.5 * ac_size, -0.5 * ac_size), (0.0, -0.25 * ac_size), (0.5 * ac_size, -0.5 * ac_size)], dtype=np.float32)
        self.ac_symbol = RenderObject(gl.GL_TRIANGLE_FAN, vertex=acvertices)
        self.ac_symbol.bind_attrib(ATTRIB_LAT, 1, self.aclatbuf, instance_divisor=1)
        self.ac_symbol.bind_attrib(ATTRIB_LON, 1, self.aclonbuf, instance_divisor=1)
        self.ac_symbol.bind_attrib(ATTRIB_ORIENTATION, 1, self.achdgbuf, instance_divisor=1)
        self.ac_symbol.bind_color(self.accolorbuf, instance_divisor=1)
        self.aclabels = self.font.prepare_text_instanced(self.aclblbuf, (8, 3), self.aclatbuf, self.aclonbuf, self.accolorbuf, char_size=text_size, vertex_offset=(ac_size, -0.5 * ac_size))

        # ------- Conflict CPA lines ---------------------
        self.cpalines = RenderObject(gl.GL_LINES, vertex=self.confcpabuf, color=amber)

        # ------- Aircraft Route -------------------------
        self.route = RenderObject(gl.GL_LINES, vertex=self.routebuf, color=magenta)
        self.routelbl = self.font.prepare_text_instanced(self.routelblbuf, (10, 2), self.routewplatbuf, self.routewplonbuf, char_size=text_size, vertex_offset=(wpt_size, 0.5 * wpt_size))
        self.routelbl.bind_color(magenta)
        rwptvertices = np.array([(-0.2 * wpt_size, -0.2 * wpt_size),
                                 ( 0.0,            -0.8 * wpt_size),
                                 ( 0.2 * wpt_size, -0.2 * wpt_size),
                                 ( 0.8 * wpt_size,  0.0),
                                 ( 0.2 * wpt_size,  0.2 * wpt_size),
                                 ( 0.0,             0.8 * wpt_size),
                                 (-0.2 * wpt_size,  0.2 * wpt_size),
                                 (-0.8 * wpt_size,  0.0)], dtype=np.float32)
        self.rwaypoints = RenderObject(gl.GL_LINE_LOOP, vertex=rwptvertices, color=magenta)
        self.rwaypoints.bind_attrib(ATTRIB_LAT, 1, self.routewplatbuf, instance_divisor=1)
        self.rwaypoints.bind_attrib(ATTRIB_LON, 1, self.routewplonbuf, instance_divisor=1)

        # --------Aircraft Trails------------------------------------------------
        self.traillines  = RenderObject(gl.GL_LINES, vertex=self.trailbuf, color=cyan)

        # ------- Waypoints ------------------------------
        wptvertices = np.array([(0.0, 0.5 * wpt_size), (-0.5 * wpt_size, -0.5 * wpt_size), (0.5 * wpt_size, -0.5 * wpt_size)], dtype=np.float32)  # a triangle
        self.nwaypoints = len(bs.navdb.wplat)
        self.waypoints = RenderObject(gl.GL_LINE_LOOP, vertex=wptvertices, color=lightblue3, n_instances=self.nwaypoints)
        # Sort based on id string length
        llid = sorted(zip(bs.navdb.wpid, bs.navdb.wplat, bs.navdb.wplon), key=lambda i: len(i[0]) > 3)
        wplat = [lat for (wpid, lat, lon) in llid]
        wplon = [lon for (wpid, lat, lon) in llid]
        self.wptlatbuf = self.waypoints.bind_attrib(ATTRIB_LAT, 1, np.array(wplat, dtype=np.float32), instance_divisor=1)
        self.wptlonbuf = self.waypoints.bind_attrib(ATTRIB_LON, 1, np.array(wplon, dtype=np.float32), instance_divisor=1)
        wptids = ''
        self.nnavaids = 0
        for wptid in llid:
            if len(wptid[0]) <= 3:
                self.nnavaids += 1
            wptids += wptid[0].ljust(5)
        self.wptlabels = self.font.prepare_text_instanced(np.array(wptids, dtype=np.string_), (5, 1), self.wptlatbuf, self.wptlonbuf, char_size=text_size, vertex_offset=(wpt_size, 0.5 * wpt_size))
        self.wptlabels.bind_color(lightblue4)
        del wptids
        self.customwp  = RenderObject(gl.GL_LINE_LOOP, vertex=wptvertices, color=lightblue3)
        self.customwp.bind_attrib(ATTRIB_LAT, 1, self.custwplatbuf, instance_divisor=1)
        self.customwp.bind_attrib(ATTRIB_LON, 1, self.custwplonbuf, instance_divisor=1)
        self.customwplbl = self.font.prepare_text_instanced(self.custwplblbuf, (5, 1), self.custwplatbuf, self.custwplonbuf, char_size=text_size, vertex_offset=(wpt_size, 0.5 * wpt_size))
        self.customwplbl.bind_color(lightblue4)
        # ------- Airports -------------------------------
        aptvertices = np.array([(-0.5 * apt_size, -0.5 * apt_size), (0.5 * apt_size, -0.5 * apt_size), (0.5 * apt_size, 0.5 * apt_size), (-0.5 * apt_size, 0.5 * apt_size)], dtype=np.float32)  # a square
        self.nairports = len(bs.navdb.aptlat)
        self.airports = RenderObject(gl.GL_LINE_LOOP, vertex=aptvertices, color=lightblue3, n_instances=self.nairports)
        indices = bs.navdb.aptype.argsort()
        aplat   = np.array(bs.navdb.aptlat[indices], dtype=np.float32)
        aplon   = np.array(bs.navdb.aptlon[indices], dtype=np.float32)
        aptypes = bs.navdb.aptype[indices]
        apnames = np.array(bs.navdb.aptid)
        apnames = apnames[indices]
        # The number of large, large+med, and large+med+small airports
        self.nairports = [aptypes.searchsorted(2), aptypes.searchsorted(3), self.nairports]

        self.aptlatbuf = self.airports.bind_attrib(ATTRIB_LAT, 1, aplat, instance_divisor=1)
        self.aptlonbuf = self.airports.bind_attrib(ATTRIB_LON, 1, aplon, instance_divisor=1)
        aptids = ''
        for aptid in apnames:
            aptids += aptid.ljust(4)
        self.aptlabels = self.font.prepare_text_instanced(np.array(aptids, dtype=np.string_), (4, 1), self.aptlatbuf, self.aptlonbuf, char_size=text_size, vertex_offset=(apt_size, 0.5 * apt_size))
        self.aptlabels.bind_color(lightblue4)
        del aptids

        # Unbind VAO, VBO
        RenderObject.unbind_all()

        # Set initial values for the global uniforms
        self.globaldata.set_wrap(self.wraplon, self.wrapdir)
        self.globaldata.set_pan_and_zoom(self.panlat, self.panlon, self.zoom)

        # Clean up memory
        del self.vbuf_asphalt, self.vbuf_concrete, self.vbuf_runways, self.vbuf_rwythr

        self.initialized = True

    def initializeGL(self):
        """Initialize OpenGL, VBOs, upload data on the GPU, etc."""

        # First check for supported GL version
        gl_version = float(gl.glGetString(gl.GL_VERSION)[:3])
        if gl_version < 3.3:
            print('OpenGL context created with GL version %.1f' % gl_version)
            qCritical("""Your system reports that it supports OpenGL up to version %.1f. The minimum requirement for BlueSky is OpenGL 3.3.
                Generally, AMD/ATI/nVidia cards from 2008 and newer support OpenGL 3.3, and Intel integrated graphics from the Haswell
                generation and newer. If you think your graphics system should be able to support GL>=3.3 please open an issue report
                on the BlueSky Github page (https://github.com/ProfHoekstra/bluesky/issues)""" % gl_version)
            return

        # background color
        gl.glClearColor(0, 0, 0, 0)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        self.globaldata = radarUBO()

        try:
            shpath = path.join(settings.gfx_path, 'shaders')
            # Compile shaders and link color shader program
            self.color_shader = BlueSkyProgram(path.join(shpath, 'radarwidget-normal.vert'), path.join(shpath, 'radarwidget-color.frag'))
            self.color_shader.bind_uniform_buffer('global_data', self.globaldata)

            # Compile shaders and link texture shader program
            self.texture_shader = BlueSkyProgram(path.join(shpath, 'radarwidget-normal.vert'), path.join(shpath, 'radarwidget-texture.frag'))
            self.texture_shader.bind_uniform_buffer('global_data', self.globaldata)

            # Compile shaders and link text shader program
            self.text_shader = BlueSkyProgram(path.join(shpath, 'radarwidget-text.vert'), path.join(shpath, 'radarwidget-text.frag'))
            self.text_shader.bind_uniform_buffer('global_data', self.globaldata)

            self.ssd_shader = BlueSkyProgram(path.join(shpath, 'ssd.vert'), path.join(shpath, 'ssd.frag'), path.join(shpath, 'ssd.geom'))
            self.ssd_shader.bind_uniform_buffer('global_data', self.globaldata)
            self.ssd_shader.loc_vlimits = gl.glGetUniformLocation(self.ssd_shader.program, 'Vlimits')
            self.ssd_shader.loc_nac = gl.glGetUniformLocation(self.ssd_shader.program, 'n_ac')

        except RuntimeError as e:
            print 'Error compiling shaders in radarwidget: ' + e.args[0]
            qCritical('Error compiling shaders in radarwidget: ' + e.args[0])
            return

        # create all vertex array objects
        try:
            self.create_objects()
        except Exception as e:
            print 'Error while creating RadarWidget objects: ' + e.args[0]

    def paintGL(self):
        """Paint the scene."""
        # pass if the framebuffer isn't complete yet or if not initialized
        if not (gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER) == gl.GL_FRAMEBUFFER_COMPLETE and self.initialized and self.isVisible()):
            return

        # Set the viewport and clear the framebuffer
        gl.glViewport(*self.viewport)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        # Send the (possibly) updated global uniforms to the buffer
        self.globaldata.set_vertex_scale_type(VERTEX_IS_LATLON)

        # --- DRAW THE MAP AND COASTLINES ---------------------------------------------
        # Map and coastlines: don't wrap around in the shader
        self.globaldata.enable_wrap(False)

        if self.show_map:
            # Select the texture shader
            self.texture_shader.use()

            # Draw map texture
            gl.glActiveTexture(gl.GL_TEXTURE0 + 0)
            gl.glBindTexture(gl.GL_TEXTURE_2D, self.map_texture)
            self.map.draw()

        # Select the non-textured shader
        self.color_shader.use()

        # Draw coastlines
        if self.show_coast:
            if self.wrapdir == 0:
                # Normal case, no wrap around
                self.coastlines.draw(first_vertex=0, vertex_count=self.vcount_coast)
            else:
                self.coastlines.bind()
                wrapindex = np.uint32(self.coastindices[int(self.wraplon) + 180])
                if self.wrapdir == 1:
                    gl.glVertexAttrib1f(ATTRIB_LON, 360.0)
                    self.coastlines.draw(first_vertex=0, vertex_count=wrapindex)
                    gl.glVertexAttrib1f(ATTRIB_LON, 0.0)
                    self.coastlines.draw(first_vertex=wrapindex, vertex_count=self.vcount_coast - wrapindex)
                else:
                    gl.glVertexAttrib1f(ATTRIB_LON, -360.0)
                    self.coastlines.draw(first_vertex=wrapindex, vertex_count=self.vcount_coast - wrapindex)
                    gl.glVertexAttrib1f(ATTRIB_LON, 0.0)
                    self.coastlines.draw(first_vertex=0, vertex_count=wrapindex)

        # --- DRAW PREVIEW SHAPE (WHEN AVAILABLE) -----------------------------
        self.polyprev.draw()

        # --- DRAW CUSTOM SHAPES (WHEN AVAILABLE) -----------------------------
        self.allpolys.draw()

        # --- DRAW THE SELECTED AIRCRAFT ROUTE (WHEN AVAILABLE) ---------------
        if self.show_traf:
            self.route.draw()
            self.cpalines.draw()
            self.traillines.draw()

        # --- DRAW AIRPORT DETAILS (RUNWAYS, TAXIWAYS, PAVEMENTS) -------------
        self.runways.draw()
        self.thresholds.draw()
        if self.zoom >= 1.0:
            for idx in self.apt_inrange:
                self.taxiways.draw(first_vertex=idx[0], vertex_count=idx[1])
                self.pavement.draw(first_vertex=idx[2], vertex_count=idx[3])

        # --- DRAW THE INSTANCED AIRCRAFT SHAPES ------------------------------
        # update wrap longitude and direction for the instanced objects
        self.globaldata.enable_wrap(True)

        # PZ circles only when they are bigger than the A/C symbols
        if self.naircraft > 0 and self.show_traf and self.show_pz and self.zoom >= 0.15:
            self.globaldata.set_vertex_scale_type(VERTEX_IS_METERS)
            self.protectedzone.draw(n_instances=self.naircraft)

        self.globaldata.set_vertex_scale_type(VERTEX_IS_SCREEN)

        # Draw traffic symbols
        if self.naircraft > 0 and self.show_traf:
            self.rwaypoints.draw(n_instances=self.routelbl.n_instances)
            self.ac_symbol.draw(n_instances=self.naircraft)

        if self.zoom >= 0.5 and self.show_apt == 1 or self.show_apt == 2:
            nairports = self.nairports[2]
        elif self.zoom  >= 0.25 and self.show_apt == 1 or self.show_apt == 3:
            nairports = self.nairports[1]
        else:
            nairports = self.nairports[0]

        if self.zoom >= 3 and self.show_wpt == 1 or self.show_wpt == 2:
            nwaypoints = self.nwaypoints
        else:
            nwaypoints = self.nnavaids

        # Draw waypoint symbols
        if self.show_wpt:
            self.waypoints.draw(n_instances=nwaypoints)
            if self.ncustwpts > 0:
                self.customwp.draw(n_instances=self.ncustwpts)

        # Draw airport symbols
        if self.show_apt:
            self.airports.draw(n_instances=nairports)

        if self.do_text:
            self.text_shader.use()
            self.font.use()

            if self.show_apt:
                self.font.set_char_size(self.aptlabels.char_size)
                self.font.set_block_size(self.aptlabels.block_size)
                self.aptlabels.draw(n_instances=nairports)
            if self.show_wpt:
                self.font.set_char_size(self.wptlabels.char_size)
                self.font.set_block_size(self.wptlabels.block_size)
                self.wptlabels.draw(n_instances=nwaypoints)
                if self.ncustwpts > 0:
                    self.customwplbl.draw(n_instances=self.ncustwpts)

            if self.show_traf and self.route.vertex_count > 1:
                self.font.set_char_size(self.routelbl.char_size)
                self.font.set_block_size(self.routelbl.block_size)
                self.routelbl.draw()

            if self.naircraft > 0 and self.show_traf and self.show_lbl:
                self.font.set_char_size(self.aclabels.char_size)
                self.font.set_block_size(self.aclabels.block_size)
                self.aclabels.draw(n_instances=self.naircraft)

        # SSD
        if self.ssd_all or self.ssd_conflicts or len(self.ssd_ownship) > 0:
            self.ssd_shader.use()
            gl.glUniform3f(self.ssd_shader.loc_vlimits, 4e4, 25e4, 500.0)
            gl.glUniform1i(self.ssd_shader.loc_nac, self.naircraft)
            self.ssd.draw(vertex_count=self.naircraft, n_instances=self.naircraft)

        # Unbind everything
        RenderObject.unbind_all()
        gl.glUseProgram(0)

    def resizeGL(self, width, height):
        """Called upon window resizing: reinitialize the viewport."""
        if not self.initialized:
            return

        # update the window size
        # Qt5 supports getting the device pixel ratio, which can be > 1 for HiDPI displays such as Mac Retina screens
        pixel_ratio = 1
        if QT_VERSION >= 5:
            pixel_ratio = self.devicePixelRatio()

        # Calculate zoom so that the window resize doesn't affect the scale, but only enlarges or shrinks the view
        zoom   = float(self.width) / float(width) * pixel_ratio
        origin = (width / 2, height / 2)

        # Update width, height, and aspect ratio
        self.width, self.height = width / pixel_ratio, height / pixel_ratio
        self.ar = float(width) / max(1, float(height))
        self.globaldata.set_win_width_height(self.width, self.height)

        self.viewport = (0, 0, width, height)

        # Update zoom
        self.event(PanZoomEvent(zoom=zoom, origin=origin))

    def update_route_data(self, data):
        if not self.initialized:
            return
        self.makeCurrent()

        self.route_acid = data.acid
        if data.acid != "" and len(data.wplat) > 0:
            nsegments = len(data.wplat)
            data.iactwp = min(max(0, data.iactwp), nsegments - 1)
            self.routelbl.n_instances = nsegments
            self.route.set_vertex_count(2 * nsegments)
            routedata = np.empty(4 * nsegments, dtype=np.float32)
            routedata[0:4] = [data.aclat, data.aclon,
                data.wplat[data.iactwp], data.wplon[data.iactwp]]

            routedata[4::4] = data.wplat[:-1]
            routedata[5::4] = data.wplon[:-1]
            routedata[6::4] = data.wplat[1:]
            routedata[7::4] = data.wplon[1:]

            update_buffer(self.routebuf, routedata)
            update_buffer(self.routewplatbuf, np.array(data.wplat, dtype=np.float32))
            update_buffer(self.routewplonbuf, np.array(data.wplon, dtype=np.float32))
            wpname = ''
            for wp, alt, spd in zip(data.wpname, data.wpalt, data.wpspd):
                if alt < 0. and spd < 0.:
                    txt = wp[:10].ljust(20)
                else:
                    txt = wp[:10].ljust(10)
                    if alt < 0:
                        txt += "-----/"
                    elif alt > 4500 * ft:
                        FL = int(round((alt / (100. * ft))))
                        txt += "FL%03d/" % FL
                    else:
                        txt += "%05d/" % int(round(alt / ft))

                    # Speed
                    if spd < 0:
                        txt += "--- "
                    else:
                        txt += "%03d " % int(round(spd / kts))

                wpname += txt
            update_buffer(self.routelblbuf, np.array(wpname))
        else:
            self.route.set_vertex_count(0)

    def update_aircraft_data(self, data):
        if not self.initialized:
            return

        self.makeCurrent()
        curnode = self.nodedata[self.iactconn]
        if curnode.filteralt:
            idx = np.where((data.alt >= curnode.filteralt[0]) * (data.alt <= curnode.filteralt[1]))
            data.lat = data.lat[idx]
            data.lon = data.lon[idx]
            data.trk = data.trk[idx]
            data.alt = data.alt[idx]
            data.tas = data.tas[idx]
        self.naircraft = len(data.lat)

        if self.naircraft == 0:
            self.cpalines.set_vertex_count(0)
        else:
            # Update data in GPU buffers
            update_buffer(self.aclatbuf, np.array(data.lat, dtype=np.float32))
            update_buffer(self.aclonbuf, np.array(data.lon, dtype=np.float32))
            update_buffer(self.achdgbuf, np.array(data.trk, dtype=np.float32))
            update_buffer(self.acaltbuf, np.array(data.alt, dtype=np.float32))
            update_buffer(self.actasbuf, np.array(data.tas, dtype=np.float32))

            # CPA lines to indicate conflicts
            ncpalines = len(data.confcpalat)

            cpalines  = np.zeros(4 * ncpalines, dtype=np.float32)
            self.cpalines.set_vertex_count(2 * ncpalines)

            # Labels and colors
            rawlabel = ''
            color    = np.empty((self.naircraft, 4), dtype=np.uint8)
            selssd   = np.zeros(self.naircraft, dtype=np.uint8)
            for i, acid in enumerate(data.id):
                # Make label: 3 lines of 8 characters per aircraft
                if data.alt[i] <= 4500. * ft:
                    rawlabel += '%-8s%-5d   %-8d' % (acid[:8], int(data.alt[i]/ft  +0.5), int(data.cas[i] / kts+0.5))
                else:
                    rawlabel += '%-8sFL%03d   %-8d' % (acid[:8], int(data.alt[i]/ft/100.+0.5), int(data.cas[i] / kts+0.5))
                confindices = data.iconf[i]
                if len(confindices) > 0:
                    if self.ssd_conflicts:
                        selssd[i] = 255
                    color[i, :] = amber + (255,)
                    for confidx in confindices:
                        cpalines[4 * confidx : 4 * confidx + 4] = [ data.lat[i], data.lon[i],
                                                                    data.confcpalat[confidx], data.confcpalon[confidx]]
                else:
                    color[i, :] = green + (255,)

                #  Check if aircraft is selected to show SSD
                if acid in self.ssd_ownship:
                    selssd[i] = 255

            if len(self.ssd_ownship) > 0 or self.ssd_conflicts:
                update_buffer(self.ssd.selssdbuf, selssd)

            update_buffer(self.confcpabuf, cpalines)
            update_buffer(self.accolorbuf, color)
            update_buffer(self.aclblbuf, np.array(rawlabel, dtype=np.string_))

            # If there is a visible route, update the start position
            if self.route_acid != "":
                if self.route_acid in data.id:
                    idx = data.id.index(self.route_acid)
                    update_buffer(self.routebuf,
                                  np.array([data.lat[idx], data.lon[idx]], dtype=np.float32))

            nact = self.nodedata[manager.sender()[0]]

            # Update trails database with new lines
            if data.swtrails:
                nact.traillat0.extend(data.traillat0)
                nact.traillon0.extend(data.traillon0)
                nact.traillat1.extend(data.traillat1)
                nact.traillon1.extend(data.traillon1)
                update_buffer(self.trailbuf, np.array(
                              zip(nact.traillat0, nact.traillon0,
                                  nact.traillat1, nact.traillon1) +
                              zip(data.traillastlat, data.traillastlon,
                                  list(data.lat), list(data.lon)),
                                       dtype=np.float32))

                self.traillines.set_vertex_count(2 * len(nact.traillat0) +
                                  2 * len(data.lat))

            else:
                nact.traillat0 = []
                nact.traillon0 = []
                nact.traillat1 = []
                nact.traillon1 = []

                self.traillines.set_vertex_count(0)

    def show_ssd(self, arg):
        if not self.initialized:
            return

        self.makeCurrent()
        if 'ALL' in arg:
            self.ssd_all      = True
            self.ssd_conflicts = False
            update_buffer(self.ssd.selssdbuf, np.ones(MAX_NAIRCRAFT, dtype=np.uint8))
        elif 'CONFLICTS' in arg:
            self.ssd_all      = False
            self.ssd_conflicts = True
        elif 'OFF' in arg:
            self.ssd_all      = False
            self.ssd_conflicts = False
            self.ssd_ownship = set()
            update_buffer(self.ssd.selssdbuf, np.zeros(MAX_NAIRCRAFT, dtype=np.uint8))
        else:
            remove = self.ssd_ownship.intersection(arg)
            self.ssd_ownship = self.ssd_ownship.union(arg) - remove

    def defwpt(self, wpdata):
        if not self.initialized:
            return
        nact = self.nodedata[manager.sender()[0]]
        nact.custwplbl += wpdata[0].ljust(5)
        nact.custwplat = np.append(nact.custwplat, np.float32(wpdata[1]))
        nact.custwplon = np.append(nact.custwplon, np.float32(wpdata[2]))

        if manager.sender()[0] == self.iactconn:
            self.makeCurrent()
            update_buffer(self.custwplblbuf, np.array(nact.custwplbl))
            update_buffer(self.custwplatbuf, nact.custwplat)
            update_buffer(self.custwplonbuf, nact.custwplon)
            self.ncustwpts = len(nact.custwplat)

    def clearNodeData(self):
        if not self.initialized:
            return

        # Clear all data for sender node
        nact = self.nodedata[manager.sender()[0]]
        nact.polynames.clear()
        nact.polydata  = np.array([], dtype=np.float32)
        nact.custwplbl = ''
        nact.custwplat = np.array([], dtype=np.float32)
        nact.custwplon = np.array([], dtype=np.float32)

        # Clear trail data
        nact.traillat0 = []
        nact.traillon0 = []
        nact.traillat1 = []
        nact.traillon1 = []

        # If the updated polygon buffer is also currently viewed, also send
        # updates to the gpu buffer
        if manager.sender()[0] == self.iactconn:
            self.allpolys.set_vertex_count(0)
            self.traillines.set_vertex_count(0)
            self.ncustwpts = 0

    def updatePolygon(self, name, data_in):
        if not self.initialized:
            return

        nact = self.nodedata[manager.sender()[0]]
        if name in nact.polynames:
            # We're either updating a polygon, or deleting it. In both cases
            # we remove the current one.
            nact.polydata = np.delete(nact.polydata, range(*nact.polynames[name]))
            del nact.polynames[name]

        # Break up polyline list of (lat,lon)s into separate line segments
        if data_in is not None:
            nact.polynames[name] = (len(nact.polydata), 2 * len(data_in))
            newbuf = np.empty(2 * len(data_in), dtype=np.float32)
            newbuf[0::4]   = data_in[0::2]  # lat
            newbuf[1::4]   = data_in[1::2]  # lon
            newbuf[2:-2:4] = data_in[2::2]  # lat
            newbuf[3:-3:4] = data_in[3::2]  # lon
            newbuf[-2:]    = data_in[0:2]
            nact.polydata  = np.append(nact.polydata, newbuf)

        # If the updated polygon buffer is also currently viewed, also send
        # updates to the gpu buffer
        if manager.sender()[0] == self.iactconn:
            self.makeCurrent()
            update_buffer(self.allpolysbuf, nact.polydata)
            self.allpolys.set_vertex_count(len(nact.polydata) / 2)

    def cmdline_stacked(self, cmd, args):
        if cmd in ['AREA', 'BOX', 'POLY', 'POLYGON', 'CIRCLE', 'LINE']:
            self.polyprev.set_vertex_count(0)

    def previewpoly(self, shape_type, data_in=None):
        if not self.initialized:
            return
        self.makeCurrent()

        if shape_type is None:
            self.polyprev.set_vertex_count(0)
            return
        if shape_type in ['BOX', 'AREA']:
            # For a box (an area is a box) we need to add two additional corners
            data = np.zeros(8, dtype=np.float32)
            data[0:2] = data_in[0:2]
            data[2:4] = data_in[2], data_in[1]
            data[4:6] = data_in[2:4]
            data[6:8] = data_in[0], data_in[3]
        else:
            data = np.array(data_in, dtype=np.float32)
        update_buffer(self.polyprevbuf, data)
        self.polyprev.set_vertex_count(len(data) / 2)

    def airportsInRange(self):
        ll_range = max(1.5 / self.zoom, 1.0)
        indices = np.logical_and.reduce((self.apt_ctrlat >= self.panlat - ll_range, self.apt_ctrlat <= self.panlat + ll_range,
                                         self.apt_ctrlon >= self.panlon - ll_range, self.apt_ctrlon <= self.panlon + ll_range))

        self.apt_inrange = self.apt_indices[indices]

    def pixelCoordsToGLxy(self, x, y):
        """Convert screen pixel coordinates to GL projection coordinates (x, y range -1 -- 1)
        """
        # GL coordinates (x, y range -1 -- 1)
        glx = (float(2.0 * x) / self.width  - 1.0)
        gly = -(float(2.0 * y) / self.height - 1.0)
        return glx, gly

    def pixelCoordsToLatLon(self, x, y):
        """Convert screen pixel coordinates to lat/lon coordinates
        """
        glx, gly = self.pixelCoordsToGLxy(x, y)

        # glxy   = zoom * (latlon - pan)
        # latlon = pan + glxy / zoom
        lat = self.panlat + gly / (self.zoom * self.ar)
        lon = self.panlon + glx / (self.zoom * self.flat_earth)
        return lat, lon

    def event(self, event):
        if not self.initialized:
            return super(RadarWidget, self).event(event)

        if event.type() == PanZoomEventType:
            if event.pan is not None:
                # Absolute pan operation
                if event.absolute:
                    self.panlat = event.pan[0]
                    self.panlon = event.pan[1]
                # Relative pan operation
                else:
                    self.panlat += event.pan[0]
                    self.panlon += event.pan[1]

                # Don't pan further than the poles in y-direction
                self.panlat = min(max(self.panlat, -90.0 + 1.0 /
                      (self.zoom * self.ar)), 90.0 - 1.0 / (self.zoom * self.ar))

                # Update flat-earth factor and possibly zoom in case of very wide windows (> 2:1)
                self.flat_earth = np.cos(np.deg2rad(self.panlat))
                self.zoom = max(self.zoom, 1.0 / (180.0 * self.flat_earth))

            if event.zoom is not None:
                if event.absolute:
                    # Limit zoom extents in x-direction to [-180:180], and in y-direction to [-90:90]
                    self.zoom = max(event.zoom, 1.0 / min(90.0 * self.ar, 180.0 * self.flat_earth))
                else:
                    prevzoom = self.zoom
                    glx, gly = self.pixelCoordsToGLxy(*event.origin)
                    self.zoom *= event.zoom

                    # Limit zoom extents in x-direction to [-180:180], and in y-direction to [-90:90]
                    self.zoom = max(self.zoom, 1.0 / min(90.0 * self.ar, 180.0 * self.flat_earth))

                    # Correct pan so that zoom actions are around the mouse position, not around 0, 0
                    # glxy / zoom1 - pan1 = glxy / zoom2 - pan2
                    # pan2 = pan1 + glxy (1/zoom2 - 1/zoom1)
                    self.panlon = self.panlon - glx * (1.0 / self.zoom - 1.0 / prevzoom) / self.flat_earth
                    self.panlat = self.panlat - gly * (1.0 / self.zoom - 1.0 / prevzoom) / self.ar

                # Don't pan further than the poles in y-direction
                self.panlat = min(max(self.panlat, -90.0 + 1.0 / (self.zoom * self.ar)), 90.0 - 1.0 / (self.zoom * self.ar))

                # Update flat-earth factor
                self.flat_earth = np.cos(np.deg2rad(self.panlat))

            if self.zoom >= 1.0:
                self.airportsInRange()
            event.accept()

            # Check for necessity wrap-around in x-direction
            self.wraplon  = -999.9
            self.wrapdir  = 0
            if self.panlon + 1.0 / (self.zoom * self.flat_earth) < -180.0:
                # The left edge of the map has passed the right edge of the screen: we can just change the pan position
                self.panlon += 360.0
            elif self.panlon - 1.0 / (self.zoom * self.flat_earth) < -180.0:
                # The left edge of the map has passed the left edge of the screen: we need to wrap around to the left
                self.wraplon = float(np.ceil(360.0 + self.panlon - 1.0 / (self.zoom * self.flat_earth)))
                self.wrapdir = -1
            elif self.panlon - 1.0 / (self.zoom * self.flat_earth) > 180.0:
                # The right edge of the map has passed the left edge of the screen: we can just change the pan position
                self.panlon -= 360.0
            elif self.panlon + 1.0 / (self.zoom * self.flat_earth) > 180.0:
                # The right edge of the map has passed the right edge of the screen: we need to wrap around to the right
                self.wraplon = float(np.floor(-360.0 + self.panlon + 1.0 / (self.zoom * self.flat_earth)))
                self.wrapdir = 1

            self.globaldata.set_wrap(self.wraplon, self.wrapdir)

            # update pan and zoom on GPU for all shaders
            self.globaldata.set_pan_and_zoom(self.panlat, self.panlon, self.zoom)
            return True

        else:
            return super(RadarWidget, self).event(event)
