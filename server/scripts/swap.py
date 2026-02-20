import psutil

mem = psutil.swap_memory()

def get_avail(mem):
    #(total - available) / total * 100
    avail = mem.percent
    print(avail)
    #return(avail)

get_avail(mem)
