'''Tools to handle aircraft data'''

import numpy

class AircraftState:
    '''The aircraft state at a specific time point'''

    def __init__(self, state):

        (self.t, self.posx, self.posy, self.posz,
         self.psi, self.tas, self.cas,
         self.sel_hdg, self.sel_spd, self.nd_range, self.nd_mode) = state

    def state_array(self):
        '''Return the state as an array'''
        return [self.t, self.posx, self.posy, self.posz,
                self.psi, self.tas, self.cas, 
                self.sel_hdg, self.sel_spd, self.nd_range, self.nd_mode]
        

class AircraftTrace:
    '''A collection of aircraft states'''

    VARIABLE_NAMES = ['t', 'posx', 'posy', 'posz',
                      'psi', 'tas', 'cas',
                      'sel_hdg', 'sel_spd', 'nd_range', 'nd_mode']

    VARIABLE_MAP = { var:idx for (idx, var) in enumerate(VARIABLE_NAMES) }
    
    def __init__(self, callsign):

        self.callsign = callsign
        self.__data   = []


    def __add_state_array(self, state_array):
        '''Append an array to the state'''
        self.__data.append(state_array)

    def __iter__(self):
        '''Return an iterator for the data'''
        return iter(self.__data)

    def addDataPoint(self, t, state):
        '''Add a data point'''
        self.__add_state_array([t]+state)

    def addAircraftState(self, acft_state):
        '''Add an aircraft state'''
        self.__add_state_array(acft_state.state_array())

    def finalize(self):
        '''Convert the list of states into a numpy array'''
        self.__data = numpy.array(self.__data)

    def reduce(self, reduced_indices):
        '''Reduce the data set to the specified range'''
        self.__data = self.__data[reduced_indices]

    def column(self, name):
        '''Get a column by name'''
        column_idx = self.VARIABLE_MAP[name]

        return numpy.array(self.__data[:,column_idx])

    def t(self, idx):
        '''Get the time of a specific state'''
        return numpy.array(self.__data[idx,0])
    
    def state(self, idx):
        '''Get the aircraft state at an index'''
        return AircraftState(self.__data[idx])

    def n_points(self):
        '''The size of the data'''
        return len(self.__data[:,0])
