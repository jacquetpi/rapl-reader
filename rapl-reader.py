import sys, getopt, re, time
from os import listdir

OUTPUT_FILE   = 'consumption.csv'
OUTPUT_HEADER = 'timestamp,domain,measure'
OUTPUT_NL     = '\n'
DELAY_S       = 5
ROOT_FS       ='/sys/class/powercap/'
LIVE_DISPLAY  = False

def print_usage():
    print('python3 rapl-reader.py [--live] [--output=' + OUTPUT_FILE + '] [--delay=' + DELAY_S + ']')

###########################################
# Find relevant sysfs
###########################################
def find_sysfs():
    regex = '^intel-rapl:[0-9]+.*$'
    folders = [f for f in listdir(ROOT_FS) if re.match(regex, f)]
    # package0: cpu, cores: cores of cpu, uncore : gpu, psys: platform ...
    sysfs = dict()
    for folder in folders:
        base = ROOT_FS + folder
        with open(base + '/name') as f: 
            domain = f.read().replace('\n','')
        if '-' not in domain: domain+= '-' + folder.split(':')[1] # We guarantee name unicity
        sysfs[domain] = base + '/energy_uj'
    return sysfs

###########################################
# Read joule file, convert to watt
###########################################
def read_rapl(sysfs : dict, hist : dict, current_time : int, time_since_launch : int):
    measures = dict()
    for domain, file in sysfs.items():
        watt = read_joule_file(domain=domain, file=file, hist=hist, current_time=current_time)
        if watt !=None: measures[domain] = watt
    # Track time for next round
    hist['time'] = current_time

    
    if measures:
        measures['package-total'] = sum([measures[domain] if 'package-' in domain else 0 for domain in measures.keys()])
        if LIVE_DISPLAY: print(measures)

        # Dump reading
        with open(OUTPUT_FILE, 'a') as f:
            for domain, measure in measures.items():
                f.write(str(time_since_launch) + ',' + domain + ',' + str(measure) + OUTPUT_NL)

def read_joule_file(domain : str, file : str, hist : dict, current_time : int):
    # Read file
    with open(file, 'r') as f: current_uj_count = int(f.read())

    # Compute delta
    current_uj_delta = current_uj_count - hist[domain] if hist[domain] != None else None
    hist[domain] = current_uj_count # Manage hist for next delta

    # Manage exceptional cases
    if current_uj_delta == None: return None # First call
    if current_uj_delta < 0: return None # Overflow

    # Convert to watt
    current_us_delta = (current_time - hist['time'])/1000 #delta with ns to us
    current_watt = current_uj_delta/current_us_delta
    
    return round(current_watt,5)

###########################################
# Main loop, read periodically
###########################################
def loop_read(sysfs : dict):
    hist = {name:None for name in sysfs.keys()}
    hist['time'] = None
    launch_at = time.time_ns()
    while True:
        time_begin = time.time_ns()
        
        read_rapl(sysfs=sysfs, hist=hist, current_time=time_begin, time_since_launch=int((time_begin-launch_at)/(10**9)))

        time_to_sleep = (DELAY_S*10**9) - (time.time_ns() - time_begin)
        if time_to_sleep>0: time.sleep(time_to_sleep/10**9)
        else: print('Warning: overlap iteration', -(time_to_sleep/10**9), 's')

###########################################
# Entrypoint, manage arguments
###########################################
if __name__ == '__main__':

    short_options = 'hld:o:'
    long_options = ['help', 'live', 'delay=5', 'output=']

    try:
        arguments, values = getopt.getopt(sys.argv[1:], short_options, long_options)
    except getopt.error as err:
        print(str(err))
        print_usage()
    for current_argument, current_value in arguments:
        if current_argument in ('-h', '--help'):
            print_usage()
        elif current_argument in('-l', '--live'):
            LIVE_DISPLAY= True
        elif current_argument in('-o', '--output'):
            OUTPUT_FILE= current_value
        elif current_argument in('-d', '--delay'):
            DELAY_S= int(current_value)
    
    try:
        # Find sysfs
        sysfs=find_sysfs()
        print('Founded topology')
        for name, location in sysfs.items(): print(name, location)
        # Init output
        with open(OUTPUT_FILE, 'w') as f: f.write(OUTPUT_HEADER + OUTPUT_NL)
        # Launch
        loop_read(sysfs=sysfs)
    except KeyboardInterrupt:
        print('Program interrupted')
        sys.exit(0)