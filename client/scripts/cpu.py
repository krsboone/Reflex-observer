import psutil

# Simple test file that just returns percent from 1 CPU
val = 1

def get_cpu(val):
    cpu = psutil.cpu_percent(interval=val)
    print(cpu)
    #return(cpu)

get_cpu(val)
