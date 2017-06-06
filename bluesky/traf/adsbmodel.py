""" ADS-B model. Implements real-life limitations of ADS-B communication."""
import numpy as np
import bluesky as bs
from bluesky.tools.aero import ft
from bluesky.tools.dynamicarrays import DynamicArrays, RegisterElementParameters


class ADSB(DynamicArrays):
    """ ADS-B model. Implements real-life limitations of ADS-B communication."""

    def __init__(self):

        # From here, define object arrays
        with RegisterElementParameters(self):
            # Most recent broadcast data
            self.lastupdate = np.array([])
            self.lat        = np.array([])
            self.lon        = np.array([])
            self.alt        = np.array([])
            self.trk        = np.array([])
            self.tas        = np.array([])
            self.gs         = np.array([])
            self.vs         = np.array([])

        self.SetNoise(False)

    def SetNoise(self, n):
        self.transnoise = n
        self.truncated  = n
        self.transerror = [1, 100, 100 * ft]  # [degree,m,m] standard bearing, distance, altitude error
        self.trunctime  = 0  # [s]

    def create(self, n=1):
        super(ADSB, self).create(n)

        self.lastupdate[-n:] = -self.trunctime * np.random.rand(n)
        self.lat[-n:] = bs.traf.lat[-n:]
        self.lon[-n:] = bs.traf.lon[-n:]
        self.alt[-n:] = bs.traf.alt[-n:]
        self.trk[-n:] = bs.traf.trk[-n:]
        self.tas[-n:] = bs.traf.tas[-n:]
        self.gs[-n:]  = bs.traf.gs[-n:]

    def update(self, time):
        up = np.where(self.lastupdate + self.trunctime < time)
        self.lat[up] = bs.traf.lat[up]
        self.lon[up] = bs.traf.lon[up]
        self.alt[up] = bs.traf.alt[up]
        self.trk[up] = bs.traf.trk[up]
        self.tas[up] = bs.traf.tas[up]
        self.gs[up]  = bs.traf.gs[up]
        self.vs[up]  = bs.traf.vs[up]
        self.lastupdate[up] = self.lastupdate[up] + self.trunctime
