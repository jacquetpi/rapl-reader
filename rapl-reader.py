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
SYSFS_FREQ    = '/sys/devices/system/cpu/{core}/cpufreq/scaling_cur_freq'
# From https://www.kernel.org/doc/Documentation/filesystems/proc.txt
SYSFS_STATS_KEYS  = {'cpuid':0, 'user':1, 'nice':2 , 'system':3, 'idle':4, 'iowait':5, 'irq':6, 'softirq':7, 'steal':8, 'guest':9, 'guest_nice':10}
SYSFS_STATS_IDLE  = ['idle', 'iowait']
SYSFS_STATS_NTID  = ['user', 'nice', 'system', 'irq', 'softirq', 'steal']
LIVE_DISPLAY = False
EXPLICIT_USAGE = None
VM_CONNECTOR = None

def print_usage():
    print('python3 rapl-reader.py [--help] [--live]  [--explicit] [--vm=qemu:///system] [--output=' + OUTPUT_FILE + '] [--delay=' + str(DELAY_S) + ' (in sec)] [--precision=' + str(PRECISION) + ' (number of decimal)]')

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
    if global_usage != None: measures['cpu%_package-global'] = round(global_usage, PRECISION)
    for numa_id, cpuid_list in cpuid_per_numa.items():
        numa_usage = get_usage_of(server_cpu_list=cpuid_list, cputime_hist=hist)
        numa_freq  = get_freq_of(server_cpu_list=cpuid_list)
        if numa_usage != None: 
            measures['cpu%_package-' + str(numa_id)] = numa_usage
            measures['freq_package-' + str(numa_id)] = numa_usage
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

    if cumulated_cpu_usage != None: 
        cumulated_cpu_usage = round(cumulated_cpu_usage/len(server_cpu_list), PRECISION)
    return cumulated_cpu_usage

def __get_usage_of_line(split : list, hist_object : object, update_history : bool = True):
    idle          = sum([ int(split[SYSFS_STATS_KEYS[idle_key]])     for idle_key     in SYSFS_STATS_IDLE])
    not_idle      = sum([ int(split[SYSFS_STATS_KEYS[not_idle_key]]) for not_idle_key in SYSFS_STATS_NTID])

    # Compute delta
    cpu_usage  = None
    if hist_object.has_time():
        prev_idle, prev_not_idle = hist_object.get_time()
        delta_idle     = idle - prev_idle
        delta_total    = (idle + not_idle) - (prev_idle + prev_not_idle)
        if delta_total>0: # Manage overflow
            cpu_usage = ((delta_total-delta_idle)/delta_total)*100
    
    if update_history: hist_object.set_time(idle=idle, not_idle=not_idle)
    return cpu_usage

def read_core_usage(cputime_hist : dict, update_history : bool):
    with open(SYSFS_STAT, 'r') as f:
        lines = f.readlines()

    measures = dict()
    lines.pop(0) # remove global line, we focus on per cpu usage
    for line in lines:
        split = line.split(' ')
        if not split[SYSFS_STATS_KEYS['cpuid']].startswith('cpu'): break

        if split[SYSFS_STATS_KEYS['cpuid']] not in cputime_hist: cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]] = CpuTime()
        cpu_usage = __get_usage_of_line(split=split, hist_object=cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]], update_history=update_history)
        measures['cpu%_' + split[SYSFS_STATS_KEYS['cpuid']]] = cpu_usage

    return measures

def get_freq_of(server_cpu_list : list):
    cumulated_cpu_freq = 0
    for cpu in server_cpu_list:
        with open(SYSFS_FREQ.replace('{core}', str(cpu)), 'r') as f:
            cumulated_cpu_freq+= int(f.read())
    return round(cumulated_cpu_freq/len(server_cpu_list), PRECISION)

###########################################
# Read libvirt
###########################################

def read_libvirt():
    count = 0
    cpu_cumul = 0
    mem_cumul = 0
    for domain_id in VM_CONNECTOR.listDomainsID():
        try:
            virDomain = VM_CONNECTOR.lookupByID(domain_id)
            cpu_cumul+=virDomain.maxVcpus()
            mem_cumul+=int(virDomain.maxMemory()/1024)
            count+=1
        except libvirt.libvirtError as ex:  # VM is not alived anymore
            pass
    return {'libvirt_vm_count': count, 'libvirt_vm_cpu_cml': cpu_cumul, 'libvirt_vm_mem_cml': mem_cumul}

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
        cpu_measures  = dict()
        if EXPLICIT_USAGE: 
            for key, value in read_core_usage(cputime_hist=cpu_hist, update_history=False).items(): cpu_measures[key] = value
        for key, value in read_cpu_usage(cpuid_per_numa=cpuid_per_numa, hist=cpu_hist).items(): cpu_measures[key] = value
        libvirt_measures = dict()
        if VM_CONNECTOR != None: libvirt_measures = read_libvirt()

        output(rapl_measures=rapl_measures, cpu_measures=cpu_measures, libvirt_measures=libvirt_measures, time_since_launch=int((time_begin-launch_at)/(10**9)))

        time_to_sleep = (DELAY_S*10**9) - (time.time_ns() - time_begin)
        if time_to_sleep>0: time.sleep(time_to_sleep/10**9)
        else: print('Warning: overlap iteration', -(time_to_sleep/10**9), 's')

def output(rapl_measures : dict, cpu_measures : dict, libvirt_measures : dict, time_since_launch : int):

    if LIVE_DISPLAY and rapl_measures:
        max_domain_length = len(max(list(rapl_measures.keys()), key=len))
        max_measure_length = len(max([str(value) for value in rapl_measures.values()], key=len))
        for domain, measure in rapl_measures.items():
            usage_complement = ''
            for package, cpu_usage in cpu_measures.items():
                if domain in package:
                    usage_complement+= '- ' + str(cpu_usage) + '%'
                    break
            print(domain.ljust(max_domain_length), str(measure).ljust(max_measure_length), 'W', usage_complement)
        if libvirt_measures: print('Libvirt:', libvirt_measures['libvirt_vm_count'], 'vm(s)', libvirt_measures['libvirt_vm_cpu_cml'], 'cpu(s)', libvirt_measures['libvirt_vm_mem_cml'], 'MB')
        if EXPLICIT_USAGE:
            print('Explicit mode: Display CPU cores exceeding 10%:')
            for cpuid, value in cpu_measures.items():
                if 'package' in cpuid: continue
                if value>=10: print(cpuid, value)
        print('---')

    # Dump reading
    with open(OUTPUT_FILE, 'a') as f:
        for domain, measure in rapl_measures.items():
            f.write(str(time_since_launch) + ',' + domain + ',' + str(measure) + OUTPUT_NL)
        for cpuid, measure in cpu_measures.items():
            f.write(str(time_since_launch) + ',' + cpuid + ',' + str(measure) + OUTPUT_NL)
        for metric, value in libvirt_measures.items():
            f.write(str(time_since_launch) + ',' + metric + ',' + str(value) + OUTPUT_NL)

###########################################
# Entrypoint, manage arguments
###########################################
if __name__ == '__main__':

    short_options = 'hledv:o:p:'
    long_options = ['help', 'live', 'explicit', 'vm=', 'delay=', 'output=', 'precision=']

    try:
        arguments, values = getopt.getopt(sys.argv[1:], short_options, long_options)
    except getopt.error as err:
        print(str(err))
        print_usage()
    for current_argument, current_value in arguments:
        if current_argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        elif current_argument in('-l', '--live'):
            LIVE_DISPLAY= True
        elif current_argument in('-e', '--explicit'):
            EXPLICIT_USAGE = True
        elif current_argument in('-v', '--vm'):
            import libvirt 
            VM_CONNECTOR = libvirt.open(current_value)
            if not VM_CONNECTOR: raise SystemExit('Failed to open connection to ' + current_value)
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
        print('>RAPL domain found:')
        max_domain_length = len(max(list(rapl_sysfs.keys()), key=len))
        for domain, location in rapl_sysfs.items(): print(domain.ljust(max_domain_length), location)
        print('>NUMA topology found:')
        for numa_id, cpu_list in cpuid_per_numa.items(): print('socket-' + str(numa_id) + ':', len(cpu_list), 'cores')
        print('')
        # Init output
        with open(OUTPUT_FILE, 'w') as f: f.write(OUTPUT_HEADER + OUTPUT_NL)
        # Launch
        loop_read(rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa)
    except KeyboardInterrupt:
        print('Program interrupted')
        sys.exit(0)
