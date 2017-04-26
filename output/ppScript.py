'''Read the logdata from a csv file into an aircraft structure'''

import csv

from acfttrace import AircraftTrace


def _strip_header(logfile):
    '''Remove the preamble from the file'''
    # Skip the first line, it is generic comment
    logfile.next()

    # The next line has the headings
    heading_string = logfile.next().strip('#')
    headings = heading_string.split()

    return headings


def _parse_aircraft_data(logfile, headings):
    '''Convert the data into an aircraft structure'''

    # Create a new list of aircraft based on the callsigns
    # aircraft = [AircraftTrace(callsign) for callsign in callsigns]

    # Loop through all the lines in the csv file and append the states
    # to the correct aircraft
    csvreader = csv.reader(logfile)

    rlen = None
    callsigns = []

    for row in csvreader:
        # print row
        callsigns.append(row[1])
        rlen = rlen or len(row)

    callsigns = list(set(callsigns))
    # print(callsigns)

    for row in csvreader:
        # The row starts with the time and scenario number
        print(row)
        # time = float(row[0])
        #
        # print(time)
        # print(callsigns)
        # for acft_id in range(len(callsigns)):
        #     print(acft_id)
            # # Compute the slice for the current aircraft
            # start_idx = 2 + 10 * acft_id
            # end_idx = 11 + 10 * acft_id + 1
            #
            # # Use list comprehension to convert the strings to the correct
            # # format. The first 8 entries are floats the last two are integers
            # state = [(float(val) if idx < 8 else int(val))
            #          for idx, val
            #          in enumerate(row[start_idx:end_idx])]
            #
            # # Finaly add the state to the aircraft data
            # aircraft[acft_id].addDataPoint(time, state)

    # for acft in aircraft:
    #     acft.finalize()
    #
    return 'asd'


def parse_logfile(filename):
    '''Parse the file and return a list of aircraft'''

    logfile = open(filename, 'r')

    headings = _strip_header(logfile)
    aircraft = _parse_aircraft_data(logfile, headings)

    return aircraft


def main():
    '''Entry point when running as a script'''

    # Check if we started with the correct arguemnts (either none or one)
    n_args = len(sys.argv)

    if n_args == 1:
        filename = 'input.txt'
    elif n_args == 2:
        filename = sys.argv[1]
    else:
        print('Too many arguments provided!')
        return 1

    aircraft = parse_logfile(filename)

    # for acft in aircraft:
    #     print(acft.callsign)


if __name__ == '__main__':
    import sys

    sys.exit(main())