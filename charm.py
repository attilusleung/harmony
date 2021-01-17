import json

class Glob:
    def __init__(self, top):
        self.top = top                  # charm.json
        self.nmegasteps = 0
        self.nmicrosteps = 0
        self.nthreads = 0

style = """
#table-wrapper {
  position:relative;
}
#table-scroll {
  height:200px;
  overflow:auto;  
}
#table-wrapper table {
  width:100%;
}
#table-wrapper table * {
  color:black;
}
#table-wrapper table thead th .text {
  position:absolute;   
  top:-20px;
  z-index:2;
  height:20px;
  width:35%;
  border:1px solid red;
}
table {
    border-collapse: collapse;
    border-style: hidden;
}
table td, table th {
    border: 1px solid black;
}
"""

def json_kv(js):
    return json_string(js["key"]) + ": " + json_string(js["value"])

def json_idx(js):
    if js["type"] == "atom":
        return json_string(js)
    return "[" + json_string(js) + "]"

def json_string(js):
    type = js["type"]
    v = js["value"]
    if type in { "bool", "int" }:
        return v
    if type == "atom":
        return "." + v
    if type == "set":
        if v == []:
            return "{}"
        return "{ " + ", ".join(v) + " }"
    if type == "dict":
        if v == []:
            return "()"
        return "dict{ " + ", ".join([ json_kv(kv) for kv in v ]) + " }" 
    if type == "pc":
        return "PC(%s)"%v
    if type == "address":
        if v == []:
            return "None"
        return "?" + v[0]["value"] + "".join([ json_idx(kv) for kv in v[1:] ])
    if type == "context":
        return "CONTEXT(" + json_string(v["name"]) + ")"

def print_vars(d):
    print("<td>")
    first = True
    for k, v in d.items():
        if first:
            first = False
        else:
            print(",", end="")
        print(" %s: %s"%(k, json_string(v)), end="")
    print("</td>")

def print_range(mis, start, end, first):
    if not first:
        print(",", end="")
    if start + 1 == end:
        print("%s"%mis[start]["pc"], end="")
    else:
        print("%s-%s"%(mis[start]["pc"], mis[end-1]["pc"]), end="")

if False:
    mis = mas["microsteps"]
    start = 0
    first = True
    for i in range(1, len(mis)):
        if "interrupt" in mis[i-1]:
            print_range(mis, start, i-1, first)
            first = False
            start = i
            print(",interrupt", end="")
        elif "choose" in mis[i]:
            print_range(mis, start, i+1, first)
            first = False
            start = i+1
            print("(choose %s)"%json_string(mis[i]["choose"]), end="")
        elif int(mis[i]["pc"]) != int(mis[i-1]["pc"]) + 1:
            print_range(mis, start, i, first)
            first = False
            start = i
    print_range(mis, start, len(mis), first)
    print("] ", end="")

def file_include(name):
    with open(name) as f:
        print(f.read())

def html_megastep(glob, step, tid, name, nmicrosteps):
    print("<tr id='mes%d'>"%(step-1))
    print("  <td align='right'>")
    print("    %d&nbsp;"%step)
    print("  </td>")

    print("  <td>")
    print("    T%s: %s"%(tid, name), end="")
    print("  </td>")

    print("  <td>")
    time = nmicrosteps
    nrows = (time + 29) // 30
    print("    <canvas id='timeline%d' width='300px' height='%dpx'>"%(step-1, 10*nrows))
    print("    </canvas>")
    print("  </td>")

    print("  <td align='center'>");
    print("  </td>")

    # print_vars(mas["shared"])
    print("  <td>");
    print("  </td>")
    print("</tr>")

def html_top(glob):
    print("<table border='1' id='mestable'>")
    print("  <thead>")
    print("    <tr>")
    print("      <th colspan='4' style='color:red;'>")
    print("        Issue:", glob.top["issue"])
    print("      </th>")
    print("      <th rowspan='2' align='center'>")
    print("        Shared Variables")
    print("      </th>")
    print("    </tr>")

    print("    <tr>")
    print("      <th align='center'>")
    print("        Step")
    print("      </th>")
    print("      <th align='center'>")
    print("        Thread")
    print("      </th>")
    print("      <th align='center'>")
    print("        Instructions")
    print("      </th>")
    print("      <th align='center'>")
    print("        &nbsp;PC&nbsp;")
    print("      </th>")
    print("    </tr>")
    print("  </thead>")

    print("  <tbody>")
    assert isinstance(glob.top["macrosteps"], list)
    nsteps = 0
    tid = None
    name = None
    nmicrosteps = 0
    for mas in glob.top["macrosteps"]:
        if tid == mas["tid"]:
            nmicrosteps += len(mas["microsteps"])
        else:
            if tid != None:
                html_megastep(glob, nsteps, tid, name, nmicrosteps)
            nsteps += 1
            tid = mas["tid"]
            name = mas["name"]
            nmicrosteps = len(mas["microsteps"])
    html_megastep(glob, nsteps, tid, name, nmicrosteps)
    print("  </tbody>")
    print("</table>")

def html_botleft(glob):
    print("<div id='table-wrapper'>")
    print("  <div id='table-scroll'>")
    print("    <table border='1'>")
    print("      <tbody>")
    for pc, instr in enumerate(glob.top["code"]):
        print("        <tr id='P%d'>"%pc)
        print("          <td align='right'>")
        print("            <a name='P%d'>%d</a>&nbsp;"%(pc, pc))
        print("          </td>")
        print("          <td>")
        print("            <span title='%s' id='C%d'>"%(glob.top["explain"][pc], pc))
        print("              %s"%instr);
        print("            </span>")
        print("          </td>")
        print("        </tr>")
    print("      </body>")
    print("    </table>")
    print("  </div>")
    print("</div>")

def html_botright(glob):
    print("<table border='1' id='threadtable'>")
    print("  <thead>")
    print("    <tr>")
    print("      <th>")
    print("        Thread")
    print("      </th>")
    print("      <th>")
    print("        Status")
    print("      </th>")
    print("      <th>")
    print("        Stack Trace")
    print("      </th>")
    print("    </tr>")
    print("  </thead>")
    print("  <tbody>")
    maxtid = 0
    for i in range(glob.nthreads):
        print("    <tr id='thread%d'>"%i)
        print("      <td align='center'>")
        print("        T%d"%i)
        print("      </td>")
        print("      <td align='center'>")
        print("        init")
        print("      </td>")
        print("      <td>")
        print("        <table id='threadinfo%d' border='1'>"%i)
        print("        </table>")
        print("      </td>")
        print("    </tr>")
    print("  </tbody>")
    print("</table>")

def html_outer(glob):
    print("<table>")
    print("  <tr>")
    print("    <td colspan='2'>")
    html_top(glob)
    print("    </td>")
    print("  </tr>")
    print("  <tr>")
    print("    <td colspan='2'>")
    print("      <h2 style='color:blue;' id='coderow'>CODE GOES HERE</h2>")
    print("    </td>")
    print("  </tr>")
    print("  <tr>")
    print("    <td valign='top'>")
    html_botleft(glob)
    print("    </td>")
    print("    <td valign='top'>")
    html_botright(glob)
    print("    </td>")
    print("  </tr>")
    print("</table>")

def html_script(glob):
    print("<script>")
    print("var nthreads = %d;"%glob.nthreads)
    print("var nmegasteps = %d;"%glob.nmegasteps)
    # print("var megasteps = [")
    # for step, mes in enumerate(top["megasteps"]):
    #     print("  {")
    #     print("    canvas: document.getElementById('timeline%d'),"%step)
    #     print("    tid: %s,"%mes["tid"])
    #     print("    nsteps: %d"%len(mes["microsteps"]))
    #     print("  },")
    # print("];")
    print("var state =")
    file_include("charm.json")
    print(";")
    file_include("charm.js")
    print("</script>")

def html_body(glob):
    print("<body>")
    html_outer(glob)
    html_script(glob)
    print("</body>")

def html_head():
    print("<head>")
    print("  <style>")
    print(style)
    print("  </style>")
    print("</head>")

def html(glob):
    print("<html>")
    html_head()
    html_body(glob)
    print("</html>")

def main():
    # First figure out how many megasteps there are and how many threads
    lasttid = -1
    with open("charm.json") as f:
        glob = Glob(json.load(f))
        assert isinstance(glob.top, dict)
        macrosteps = glob.top["macrosteps"]
        for mas in macrosteps:
            tid = int(mas["tid"])
            if tid >= glob.nthreads:
                glob.nthreads = tid + 1
            if tid != lasttid:
                glob.nmegasteps += 1
                lasttid = tid
            glob.nmicrosteps += len(mas["microsteps"])

    html(glob)

main()
