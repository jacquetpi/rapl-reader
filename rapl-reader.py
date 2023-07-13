import sys, getopt, re, time
from os import listdir
from os.path import isfile, join

OUTPUT_FILE   = 'consumption.csv'
OUTPUT_HEADER = 'timestamp,domain,measure'
OUTPUT_NL     = '\n'
DELAY_S       = 5
ROOT_FS       ='/sys/class/powercap/'
PRECISION     = 5
SYSFS_STAT    = '/proc/stat'
SYSFS_TOPO    = '/sys/devices/system/cpu/'
# From https://www.kernel.org/doc/Documentation/filesystems/proc.txt
SYSFS_STATS_KEYS  = {'cpuid':0, 'user':1, 'nice':2 , 'system':3, 'idle':4, 'iowait':5, 'irq':6, 'softirq':7, 'steal':8, 'guest':9, 'guest_nice':10}
SYSFS_STATS_IDLE  = ['idle', 'iowait']
SYSFS_STATS_NTID  = ['user', 'nice', 'system', 'irq', 'softirq', 'steal']
LIVE_DISPLAY  = False

def print_usage():
    print('python3 rapl-reader.py [--live] [--output=' + OUTPUT_FILE + '] [--delay=' + str(DELAY_S) + '] [--precision=' + str(PRECISION) + ']')

###########################################
# Find relevant sysfs
###########################################
def find_rapl_sysfs():
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

def find_cpuid_per_numa():
    regex = '^cpu[0-9]+$'
    cpu_found = [int(re.sub("[^0-9]", '', f)) for f in listdir(SYSFS_TOPO) if not isfile(join('topology', f)) and re.match(regex, f)]
    cpu_per_numa = dict()
    for cpu in cpu_found:
        with open(SYSFS_TOPO + 'cpu' + str(cpu) + '/topology/physical_package_id', 'r') as f:
            numa_id = int(f.read())
        if numa_id not in cpu_per_numa: cpu_per_numa[numa_id] = list()
        cpu_per_numa[numa_id].append('cpu' + str(cpu))
    return cpu_per_numa

###########################################
# Read CPU usage
###########################################
def read_cpu_usage(cpuid_per_numa : dict, hist :dict):
    measures = dict()
    global_usage = get_usage_global(cputime_hist=hist)
    if global_usage != None: measures['cpu%_package-global'] = global_usage
    for numa_id, cpuid_list in cpuid_per_numa.items():
        numa_usage = get_usage_of(server_cpu_list=cpuid_list, cputime_hist=hist)
        if numa_usage != None: measures['cpu%_package-' + str(numa_id)] = global_usage
    return measures

class CpuTime(object):
    def has_time(self):
        return hasattr(self, 'idle') and hasattr(self, 'not_idle')

    def set_time(self, idle : int, not_idle : int):
        setattr(self, 'idle', idle)
        setattr(self, 'not_idle', not_idle)

    def get_time(self):
        return getattr(self, 'idle'), getattr(self, 'not_idle')

    def clear_time(self):
        if hasattr(self, 'idle'): delattr(self, 'idle')
        if hasattr(self, 'not_idle'): delattr(self, 'not_idle')

def get_usage_global(cputime_hist : dict):
    with open(SYSFS_STAT, 'r') as f:
        split = f.readlines()[0].split(' ')
        split.remove('')
    if 'global' not in cputime_hist: cputime_hist['global'] = CpuTime()
    return __get_usage_of_line(split=split, hist_object=cputime_hist['global'])

def get_usage_of(server_cpu_list : list, cputime_hist : dict):
    cumulated_cpu_usage = 0
    with open(SYSFS_STAT, 'r') as f:
        lines = f.readlines()

    for line in lines:
        split = line.split(' ')
        if not split[SYSFS_STATS_KEYS['cpuid']].startswith('cpu'): break
        if split[SYSFS_STATS_KEYS['cpuid']] not in server_cpu_list: continue

        if split[SYSFS_STATS_KEYS['cpuid']] not in cputime_hist: cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]] = CpuTime()
        hist_object = cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]]

        cpu_usage = __get_usage_of_line(split=split, hist_object=hist_object)
    
        # Add usage to cumulated value
        if cumulated_cpu_usage != None and cpu_usage != None:
            cumulated_cpu_usage+=cpu_usage
        else: cumulated_cpu_usage = None # Do not break to compute others initializing values

    return cumulated_cpu_usage

def __get_usage_of_line(split : list, hist_object : object):
    idle          = sum([ int(split[SYSFS_STATS_KEYS[idle_key]])     for idle_key     in SYSFS_STATS_IDLE])
    not_idle      = sum([ int(split[SYSFS_STATS_KEYS[not_idle_key]]) for not_idle_key in SYSFS_STATS_NTID])

    # Compute delta
    cpu_usage  = None
    if hist_object.has_time():
        prev_idle, prev_not_idle = hist_object.get_time()
        delta_idle     = idle - prev_idle
        delta_total    = (idle + not_idle) - (prev_idle + prev_not_idle)
        if delta_total>0: # Manage overflow
            cpu_usage = round(((delta_total-delta_idle)/delta_total)*100,PRECISION)
    hist_object.set_time(idle=idle, not_idle=not_idle)
    return cpu_usage

###########################################
# Read joule file, convert to watt
###########################################
def read_rapl(rapl_sysfs : dict, hist : dict, current_time : int):
    measures = dict()
    overflow = False
    package_global = 0
    for domain, file in rapl_sysfs.items():
        watt = read_joule_file(domain=domain, file=file, hist=hist, current_time=current_time)
        if watt !=None: 
            measures[domain] = round(watt,PRECISION)
            if 'package-' in domain: package_global+=watt
        else: overflow=True

    # Track time for next round
    hist['time'] = current_time

    if measures:
        if not overflow: measures['package-global'] = round(package_global,PRECISION)
    return measures

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
    
    return current_watt

###########################################
# Main loop, read periodically
###########################################
def loop_read(rapl_sysfs : dict, cpuid_per_numa : dict):
    rapl_hist = {name:None for name in rapl_sysfs.keys()}
    rapl_hist['time'] = None # for joule to watt conversion
    cpu_hist = dict()
    launch_at = time.time_ns()
    while True:
        time_begin = time.time_ns()
        
        rapl_measures = read_rapl(rapl_sysfs=rapl_sysfs, hist=rapl_hist, current_time=time_begin)
        cpu_measures  = read_cpu_usage(cpuid_per_numa=cpuid_per_numa, hist=cpu_hist)
        output(rapl_measures=rapl_measures, cpu_measures=cpu_measures, time_since_launch=int((time_begin-launch_at)/(10**9)))

        time_to_sleep = (DELAY_S*10**9) - (time.time_ns() - time_begin)
        if time_to_sleep>0: time.sleep(time_to_sleep/10**9)
        else: print('Warning: overlap iteration', -(time_to_sleep/10**9), 's')

def output(rapl_measures : dict, cpu_measures : dict, time_since_launch : int):
    if LIVE_DISPLAY and rapl_measures: 
        for domain, measure in rapl_measures.items():
            usage_complement = ''
            for package, cpu_usage in cpu_measures.items():
                if domain in package:
                    usage_complement+= '- ' + str(cpu_usage) + '%'
                    break
            print(domain, measure, usage_complement)

    # Dump reading
    with open(OUTPUT_FILE, 'a') as f:
        for domain, measure in rapl_measures.items():
            f.write(str(time_since_launch) + ',' + domain + ',' + str(measure) + OUTPUT_NL)
        for cpuid, measure in cpu_measures.items():
            f.write(str(time_since_launch) + ',' + cpuid + ',' + str(measure) + OUTPUT_NL)

###########################################
# Entrypoint, manage arguments
###########################################
if __name__ == '__main__':

    short_options = 'hld:o:p:'
    long_options = ['help', 'live', 'delay=', 'output=', 'precision=']

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
        elif current_argument in('-p', '--precision'):
            PRECISION= int(current_value)
        elif current_argument in('-d', '--delay'):
            DELAY_S= float(current_value)
    
    try:
        # Find sysfs
        rapl_sysfs=find_rapl_sysfs()
        cpuid_per_numa=find_cpuid_per_numa()
        print('RAPL domain found:')
        for domain, location in rapl_sysfs.items(): print(domain, location)
        print('NUMA topology found:')
        for numa_id, cpu_list in cpuid_per_numa.items(): 
            cpu_list.sort()
            print(numa_id, cpu_list)
        # Init output
        with open(OUTPUT_FILE, 'w') as f: f.write(OUTPUT_HEADER + OUTPUT_NL)
        # Launch
        loop_read(rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa)
    except KeyboardInterrupt:
        print('Program interrupted')
        sys.exit(0)