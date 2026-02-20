import psutil

mem = psutil.virtual_memory()

def get_avail(mem):
    print(mem.percent)
    #return(mem.percent)

get_avail(mem)
