"""
	This is the Harmony compiler.

    Copyright (C) 2020  Robbert van Renesse

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions
    are met:

    1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

    3. Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived
    from this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
    FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
    INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
    BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
    LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
    CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
    LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
    ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.
"""

import sys
import os
import getopt
import traceback
import collections
import time
import math
import html

# TODO.  This should not be global ideally
files = {}              # files that have been read already
constants = {}          # constants modified with -c
modules = {}            # modules modified with -m
namestack = []          # stack of module names being compiled
node_uid = 1            # unique node identifier

################################
######### Synch module ######### 
################################
mod_synch = """
import list;
import bag;

def tas(lk):
    atomic:
        result = ^lk;
        ^lk = True;
    ;
;
def Lock():
    result = False;
;
def lock(lk):
    while tas(lk):
        pass;
    ;
;
def unlock(lk):
    ^lk = False;
;
def Condition(lk):
    result = dict{ .lock: lk, .waiters: bagEmpty() };
;
def wait(c):
    let lk = (), blocked = True, cnt = 0:
        atomic:
            lk = (^c).lock;
            cnt = bagCount((^c).waiters, nametag());
            bagAdd(&(^c).waiters, nametag());
            ^lk = False;
        ;
        while blocked:
            atomic:
                if (not (^lk)) and (bagCount((^c).waiters, nametag()) <= cnt):
                    ^lk = True;
                    blocked = False;
                ;
            ;
        ;
    ;
;
def notify(c):
    atomic:
        let waiters = (^c).waiters:
            if waiters != bagEmpty():
                bagRemove(&(^c).waiters, bagChoose(waiters));
            ;
        ;
    ;
;
def notifyAll(c):
    (^c).waiters = bagEmpty();
;
def Semaphore(cnt):
    result = cnt;
;
def P(sema):
    let blocked = True:
        while blocked:
            atomic:
                if (^sema) > 0:
                    ^sema -= 1;
                    blocked = False;
                ;
            ;
        ;
    ;
;
def V(sema):
    atomic:
        ^sema += 1;
    ;
;
def Queue():
    result = [];
;
def dequeue(q):
    let blocked = True:
        while blocked:
            atomic:
                if ^q != []:
                    result = head(^q);
                    ^q = tail(^q);
                    blocked = False;
                ;
            ;
        ;
    ;
;
def enqueue(q, item):
    atomic:
        ^q = append(^q, item);
    ;
;
"""
#################################

#################################
######### SynchS module ######### 
#################################
mod_synchS = """
import list;

def Lock():
    result = dict{ .locked: False, .suspended: [] };
;
def lock(lk):
    atomic:
        if (^lk).locked:
            stop (^lk).suspended;
            assert (^lk).locked;
        else:
            (^lk).locked = True;
        ;
    ;
;
def unlock(lk):
    atomic:
        if (^lk).suspended == []:
            (^lk).locked = False;
        else:
            go (head((^lk).suspended)) ();
            (^lk).suspended = tail((^lk).suspended);
        ;
    ;
;
def Condition(lk):
    result = dict{ .lock: lk, .waiters: [] };
;
def wait(c):
    atomic:
        unlock((^c).lock);
        stop (^c).waiters;
    ;
;
def notify(c):
    atomic:
        let lk = (^c).lock, waiters = (^c).waiters:
            if waiters != []:
                (^lk).suspended += [waiters[0],];
                (^c).waiters = tail(waiters);
            ;
        ;
    ;
;
def notifyAll(c):
    atomic:
        let lk = (^c).lock, waiters = (^c).waiters:
            (^lk).suspended += waiters;
            (^c).waiters = [];
        ;
    ;
;
def Semaphore(cnt):
    result = dict{ .count: cnt, .waiters: [] };
;
def P(sema):
    atomic:
        if (^sema).count > 0:
            (^sema).count -= 1;
        else:
            stop (^sema).waiters;
        ;
    ;
;
def V(sema):
    atomic:
        let cnt = (^sema).count, waiters = (^sema).waiters:
            if waiters != []:
                assert cnt == 0;
                go (waiters[0]) ();
                (^sema).waiters = tail(waiters);
            else:
                (^sema).count = cnt + 1;
            ;
        ;
    ;
;
def Queue():
    result = dict{ list: [], waiters: [] };
;
def dequeue(q):
    atomic:
        let list = (^q).list:
            if list == []:
                stop (^q).waiters;
            ;
            result = head(list);
            (^q).list = tail(list);
        ;
    ;
;
def enqueue(q, item):
    atomic:
        (^q).list = append((^q).list, item);
        let waiters = (^q).waiters:
            if waiters != []:
                go (waiters[0]) item;
                (^q).waiters = tail(waiters);
            ;
        ;
    ;
;
"""
#################################

################################
######### List module ######### 
################################
mod_list = """
# return s[b:e]
def subseq(s, b, e):
    result = [];
    for x in b..(e-1):
        result[x - b] = s[x];
    ;
;

def append(s, e):
    result = s + [e,];
;
def head(s):
    result = s[0];
;
def tail(s):
    result = subseq(s, 1, len(s));
;

# quicksort of a list
def listQsort(a):
    if a == []:
        result = [];
    else:
        let i = 1, pivot = a[0], lower = [], higher = []:
            while i < len(a):
                if a[i] < pivot:
                    lower += [a[i],];
                else:
                    higher += [a[i],];
                ;
                i = i + 1;
            ;
            result = listQsort(lower) + [pivot,] + listQsort(higher);
        ;
    ;
;

# sum of a list
def listSum(a):
    result = 0;
    for i in keys(a):
        result += a[i];
    ;
;

def list2set(a):
    result = { a[i] for i in keys(a) };
;

# turn a list into a bag (multiset)
def list2bag(a):
    result = dict{};
    for i in keys(a):
        if a[i] in keys(result):
            result[a[i]] += 1;
        else:
            result[a[i]] = 1;
        ;
    ;
;

def listMin(a):
    result = min(list2set(a));
;
def listMax(a):
    result = max(list2set(a));
;
"""
#################################

##############################
######### Bag module ######### 
##############################
mod_bag = """
def bagEmpty():
    result = dict{};
;
def bagFromSet(s):
    result = dict{ 1 for elt in s };
;
def bagCount(bag, elt):
    if elt in keys(bag):
        result = bag[elt];
    else:
        result = 0;
    ;
;
def bagChoose(bag):
    # TODO.  Assert that no elements are mapped to 0
    assert 0 not in { bag[k] for k in keys(bag) };
    result = choose(keys(bag));
;
def bagAdd(pb, elt):
    if elt in keys(^pb):
        (^pb)[elt] += 1;
    else:
        (^pb)[elt] = 1;
    ;
;
def bagRemove(pb, elt):
    if (elt in keys(^pb)) and ((^pb)[elt] > 0):
        (^pb)[elt] -= 1;
    ;
    if (^pb)[elt] <= 0:
        del (^pb)[elt];
    ;
;
"""
##############################

################################
######### Alloc module ######### 
################################
mod_alloc = """
const NPOOL  = 100;      # maximum number of "records" to allocate

def recFree(r):
    lock(&rec_lock);
    (^r).next = rec_flist;
    rec_flist = r;
    unlock(&rec_lock);
;
def recAlloc():
    lock(&rec_lock);
    result = rec_flist;
    if result != None:
        rec_flist = (^result).next;
    ;
    unlock(&rec_lock);
;
rec_pool = [ dict{ .data: (), .next: None } for i in 0..(NPOOL-1) ];
rec_flist = None;           # free list
rec_lock = Lock();
for i in 0..(NPOOL-1):
    recFree(&rec_pool[i]);
;
"""
##############################

internal_modules = {
    "synch":  mod_synch,
    "synchS": mod_synchS,
    "list":   mod_list,
    "bag":    mod_bag,
    "alloc":  mod_alloc,
}

def load_string(all, filename, scope, code):
    files[filename] = all.split("\n")
    tokens = lexer(all, filename)
    try:
        (ast, rem) = StatListRule(set()).parse(tokens)
    except IndexError:
        # best guess...
        print("Parsing", filename, "hit EOF (usually missing ';' at end of last line)")
        # print(traceback.format_exc())
        exit(1)
    ast.compile(scope, code)

def load(f, filename, scope, code):
    if filename in files:
        return
    namestack.append(filename)
    all = ""
    for line in f:
        all += line
    load_string(all, filename, scope, code)
    namestack.pop()

def islower(c):
    return c in "abcdefghijklmnopqrstuvwxyz"

def isupper(c):
    return c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def isletter(c):
    return islower(c) or isupper(c)

def isnumeral(c):
    return c in "0123456789"

def isalnum(c):
    return isletter(c) or isnumeral(c)

def isnamechar(c):
    return isalnum(c) or c == "_"

def isprint(c):
    return isinstance(c, str) and len(c) == 1 and (
        isalnum(c) or c in " ~`!@#$%^&*()-_=+[{]}\\|;:'\",<.>/?")

def isnumber(s):
    return all(isnumeral(c) for c in s)

def isreserved(s):
    return s in [
        "and",
        "assert",
        "atLabel",
        "atomic",
        "bagsize",
        "call",
        "cardinality",
        "choose",
        "const",
        "def",
        "del",
        "disable",
        "else",
        "enable",
        "False",
        "fun",
        "for",
        "go",
        "hash",
        "if",
        "import",
        "in",
        "inf",
        "keys",
        "len",
        "let",
        "max",
        "min",
        "nametag",
        "None",
        "not",
        "or",
        "pass",
        "spawn",
        "stop",
        "trap",
        "True",
        "while"
    ]

def isname(s):
    return (not isreserved(s)) and (isletter(s[0]) or s[0] == "_") and \
                    all(isnamechar(c) for c in s)

def isunaryop(s):
    return s in [ "^", "-", "atLabel", "bagsize", "cardinality", "choose",
        "min", "max", "nametag", "not", "keys", "hash", "len", "processes" ]

def isbinaryop(s):
    return s in [
        "==", "!=", "..", "in", "and", "or",
        "-", "+", "*", "/", "%", "<", "<=", ">", ">="
    ];

tokens = [ "dict{", "==", "!=", "<=", ">=", ".." ]

def lexer(s, file):
    result = []
    line = 1
    column = 1
    while s != "":
        # see if it's a blank
        if s[0] in { " ", "\t" }:
            s = s[1:]
            column += 1
            continue

        if s[0] == "\n":
            s = s[1:]
            line += 1
            column = 1
            continue

        # skip over line comments
        if s.startswith("#"):
            s = s[1:]
            while len(s) > 0 and s[0] != '\n':
                s = s[1:]
            continue

        # skip over nested comments
        if s.startswith("(*"):
            count = 1
            s = s[2:]
            column += 2
            while count != 0 and s != "":
                if s.startswith("(*"):
                    count += 1
                    s = s[2:]
                    column += 2
                elif s.startswith("*)"):
                    count -= 1
                    s = s[2:]
                    column += 2
                elif s[0] == "\n":
                    s = s[1:]
                    line += 1
                    column = 1
                else:
                    s = s[1:]
                    column += 1
            continue

        # see if it's a multi-character token.  Match with the longest one
        found = ""
        for t in tokens:
            if s.startswith(t) and len(t) > len(found):
                found = t
        if found != "":
            result += [ (found, file, line, column) ]
            s = s[len(found):]
            column += len(found)
            continue

        # see if a sequence of letters and numbers
        if isnamechar(s[0]):
            i = 0
            while i < len(s) and isnamechar(s[i]):
                i += 1
            result += [ (s[:i], file, line, column) ]
            s = s[i:]
            column += i
            continue

        # string
        if s[0] == '"':
            i = 1
            str = '"'
            while i < len(s) and s[i] != '"':
                if s[i] == '\\':
                    i += 1
                    if i == len(s):
                        break
                    if s[i] == '"':
                        str += '"'
                    elif s[i] == '\\':
                        str += '\\'
                    elif s[i] == 't':
                        str += '\t'
                    elif s[i] == 'n':
                        str += '\n'
                    elif s[i] == 'f':
                        str += '\f'
                    elif s[i] == 'r':
                        str += '\r'
                    else:
                        str += s[i]
                else:
                    str += s[i]
                i += 1
            if i < len(s):
                i += 1
            str += '"'
            result += [ (str, file, line, column) ]
            s = s[i:]
            column += i
            continue

        # everything else is a single character token
        result += [ (s[0], file, line, column) ]
        s = s[1:]
        column += 1
    return result

def strValue(v):
    if isinstance(v, Value) or isinstance(v, bool) or isinstance(v, int) or isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        return "." + v
    assert False, v

def strVars(v):
    assert isinstance(v, DictValue)
    result = ""
    for (var, val) in v.d.items():
        if result != "":
            result += ", "
        result += strValue(var)[1:] + "=" + strValue(val)
    return "{" + result + "}"

def keyValue(v):
    if isinstance(v, bool):
        return (0, v)
    if isinstance(v, int) or isinstance(v, float):
        return (1, v)
    if isinstance(v, str):
        return (2, v)
    assert isinstance(v, Value)
    return v.key()

class Value:
    def __str__(self):
        return self.__repr__()

class PcValue(Value):
    def __init__(self, pc):
        self.pc = pc

    def __repr__(self):
        return "PC(" + str(self.pc) + ")"

    def __hash__(self):
        return self.pc.__hash__()

    def __eq__(self, other):
        return isinstance(other, PcValue) and other.pc == self.pc

    def key(self):
        return (3, self.pc)

class DictValue(Value):
    def __init__(self, d):
        self.d = d

    def __repr__(self):
        if len(self.d) == 0:
            return "()"
        result = ""
        if set(self.d.keys()) == set(range(len(self.d))):
            for k in range(len(self.d)):
                if result != "":
                    result += ", ";
                result += strValue(self.d[k])
            return "[" + result + "]"
        keys = sorted(self.d.keys(), key=keyValue)
        for k in keys:
            if result != "":
                result += ", ";
            result += strValue(k) + ":" + strValue(self.d[k])
        return "dict{ " + result + " }"

    def __hash__(self):
        hash = 0
        for x in self.d.items():
            hash ^= x.__hash__()
        return hash

    def __eq__(self, other):
        if not isinstance(other, DictValue):
            return False
        if len(self.d.keys()) != len(other.d.keys()):   # for efficiency
            return False
        return self.d == other.d

    def __len__(self):
        return len(self.d.keys())

    # Dictionary ordering generalizes lexicographical ordering when the dictionary
    # represents a list or tuple
    def key(self):
        return (5, [ (keyValue(v), keyValue(self.d[v]))
                        for v in sorted(self.d.keys(), key=keyValue)])

# TODO.  Is there a better way than making this global?
novalue = DictValue({})

class SetValue(Value):
    def __init__(self, s):
        self.s = s

    def __repr__(self):
        if len(self.s) == 0:
            return "{}"
        result = ""
        vals = sorted(self.s, key=keyValue)
        for v in vals:
            if result != "":
                result += ", ";
            result += strValue(v)
        return "{ " + result + " }"

    def __hash__(self):
        return frozenset(self.s).__hash__()

    def __eq__(self, other):
        if not isinstance(other, SetValue):
            return False
        return self.s == other.s

    def key(self):
        return (6, [keyValue(v) for v in sorted(self.s, keyValue)])

class AddressValue(Value):
    def __init__(self, indexes):
        self.indexes = indexes

    def __repr__(self):
        if len(self.indexes) == 0:
            return "None"
        result = "&" + self.indexes[0]
        for index in self.indexes[1:]:
            if isinstance(index, str):
                result = result + strValue(index)
            else:
                result += "[" + strValue(index) + "]"
        return result

    def __hash__(self):
        hash = 0
        for x in self.indexes:
            hash ^= x.__hash__()
        return hash

    def __eq__(self, other):
        if not isinstance(other, AddressValue):
            return False
        return self.indexes == other.indexes

    def key(self):
        return (7, self.indexes)

class Op:
    def explain(self):
        return "no explanation yet"

# Splits a non-empty set in its minimum element and its remainder
class CutOp(Op):
    def __repr__(self):
        return "Cut"

    def explain(self):
        return "pops a set value and pushes the smallest element and the remaining set"

    def eval(self, state, context):
        v = context.pop()
        assert isinstance(v, SetValue), v
        assert v.s != set()
        lst = sorted(v.s, key=keyValue)
        context.push(lst[0])
        context.push(SetValue(set(lst[1:])))
        context.pc += 1

# Splits a tuple into its elements
class SplitOp(Op):
    def __repr__(self):
        return "Split"

    def explain(self):
        return "splits a tuple value into its elements"

    def eval(self, state, context):
        v = context.pop()
        assert isinstance(v, DictValue), v
        for i in range(len(v.d)):
            context.push(v.d[i])
        context.pc += 1

# Move an item in the stack to the top
class MoveOp(Op):
    def __init__(self, offset):
        self.offset = offset

    def __repr__(self):
        return "Move %d"%self.offset

    def explain(self):
        return "move stack element to top"

    def eval(self, state, context):
        v = context.stack.pop(len(context.stack) - self.offset)
        context.push(v)
        context.pc += 1

class DupOp(Op):
    def __repr__(self):
        return "Dup"

    def explain(self):
        return "push a copy of the top value on the stack"

    def eval(self, state, context):
        v = context.pop()
        context.push(v)
        context.push(v)
        context.pc += 1

class GoOp(Op):
    def __repr__(self):
        return "Go"

    def explain(self):
        return "pops a context and a value, restores the corresponding process, and pushes the value on its stack"

    def eval(self, state, context):
        ctx = context.pop()
        if not isinstance(ctx, ContextValue):
            context.failure = "pc = " + str(context.pc) + \
                ": Error: expected context value, got " + str(ctx)
        else:
            if ctx in state.stopbag:
                cnt = state.stopbag[ctx]
                assert cnt > 0
                if cnt == 1:
                    del state.stopbag[ctx]
                else:
                    state.stopbag[ctx] = cnt - 1
            result = context.pop();
            copy = ctx.copy()
            copy.push(result)
            copy.stopped = False
            state.add(copy)
            context.pc += 1

class LoadVarOp(Op):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        if self.name == None:
            return "LoadVar"
        else:
            (lexeme, file, line, column) = self.name
            return "LoadVar " + str(lexeme)

    def explain(self):
        if self.name == None:
            return "pop the address of a method variable and push the value of that variable"
        else:
            return "push the value of method variable " + self.name[0]

    def eval(self, state, context):
        if self.name == None:
            av = context.pop()
            assert isinstance(av, AddressValue)
            context.push(context.iget(av.indexes))
        else:
            (lexeme, file, line, column) = self.name
            context.push(context.get(lexeme))
        context.pc += 1

class PushOp(Op):
    def __init__(self, constant):
        self.constant = constant

    def __repr__(self):
        (lexeme, file, line, column) = self.constant
        return "Push " + strValue(lexeme)

    def explain(self):
        return "push constant " + strValue(self.constant[0])

    def eval(self, state, context):
        (lexeme, file, line, column) = self.constant
        context.push(lexeme)
        context.pc += 1

class PushAddressOp(Op):
    def __init__(self, name):
        self.name = name 

    def __repr__(self):
        (lexeme, file, line, column) = self.name
        return "PushAddress " + str(lexeme)

    def explain(self):
        return "push the address of shared variable " + self.name[0]

    def eval(self, state, context):
        (lexeme, file, line, column) = self.name
        context.push(AddressValue([lexeme]))
        context.pc += 1

class LoadOp(Op):
    def __init__(self, name, token):
        self.name = name
        self.token = token

    def __repr__(self):
        if self.name == None:
            return "Load"
        else:
            (lexeme, file, line, column) = self.name
            return "Load " + lexeme

    def explain(self):
        if self.name == None:
            return "pop an address and push the value at the address"
        else:
            return "push value of shared variable " + self.name[0]

    def eval(self, state, context):
        if self.name == None:
            av = context.pop()
            if not isinstance(av, AddressValue):
                context.failure = "Error: not an address " + \
                                    str(self.token) + " -> " + str(av)
                return
            context.push(state.iget(av.indexes))
        else:
            (lexeme, file, line, column) = self.name
            if lexeme not in state.vars.d:
                context.failure = "Error: no variable " + str(self.token)
                return
            context.push(state.get(lexeme))
        context.pc += 1

class StoreOp(Op):
    def __init__(self, name, token):
        self.name = name
        self.token = token  # for error reporting

    def __repr__(self):
        if self.name != None:
            (lexeme, file, line, column) = self.name
            return "Store " + lexeme
        else:
            return "Store"

    def explain(self):
        if self.name == None:
            return "pop a value and an address and store the value at the address"
        else:
            return "pop a value and store it in shared variable " + self.name[0]

    def eval(self, state, context):
        v = context.pop()
        if self.name == None:
            av = context.pop()
            if not isinstance(av, AddressValue):
                context.failure = "Error: not an address " + \
                                    str(self.token) + " -> " + str(av)
                return
            lv = av.indexes
            name = lv[0]
        else:
            (lexeme, file, line, column) = self.name
            lv = [lexeme]
            name = lexeme

        if not state.initializing and (name not in state.vars.d):
            context.failure = "Error: using an uninitialized shared variable " \
                    + name + ": " + str(self.token)
        else:
            try:
                state.set(lv, v)
                context.pc += 1
            except AttributeError:
                context.failure = "Error: " + name + " is not a dictionary " + str(self.token)

class DelOp(Op):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        if self.name != None:
            (lexeme, file, line, column) = self.name
            return "Del " + lexeme
        else:
            return "Del"

    def explain(self):
        if self.name == None:
            return "pop an address and delete the shared variable at the address"
        else:
            return "delete the shared variable " + self.name[0]

    def eval(self, state, context):
        if self.name == None:
            av = context.pop()
            if not isinstance(av, AddressValue):
                context.failure = "Error: not an address " + \
                                    str(self.token) + " -> " + str(av)
                return
            lv = av.indexes
            name = lv[0]
        else:
            (lexeme, file, line, column) = self.name
            lv = [lexeme]
            name = lexeme

        if not state.initializing and (name not in state.vars.d):
            context.failure = "Error: deleting an uninitialized shared variable " \
                    + name + ": " + str(self.token)
        else:
            state.delete(lv)
            context.pc += 1

class StopOp(Op):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        if self.name != None:
            (lexeme, file, line, column) = self.name
            return "Stop " + lexeme
        else:
            return "Stop"

    def explain(self):
        if self.name == None:
            return "pop an address and store context at that address"
        else:
            return "store context at " + self.name[0]

    def eval(self, state, context):
        if self.name == None:
            av = context.pop()
            if not isinstance(av, AddressValue):
                context.failure = "Error: not an address " + \
                                    str(self.token) + " -> " + str(av)
                return
            lv = av.indexes
            name = lv[0]
        else:
            (lexeme, file, line, column) = self.name
            lv = [lexeme]
            name = lexeme

        if not state.initializing and (name not in state.vars.d):
            context.failure = "Error: using an uninitialized shared variable " \
                    + name + ": " + str(self.token)
        else:
            # Update the context before saving it
            context.stopped = True
            context.pc += 1
            assert isinstance(state.code[context.pc], ContinueOp)

            # Save the context
            state.stop(lv, context)

class ContinueOp(Op):
    def __repr__(self):
        return "Continue"

    def explain(self):
        return "a no-op, must follow a Stop operation"

    def eval(self, state, context):
        context.pc += 1

class AddressOp(Op):
    def __init__(self, n):
        self.n = n          # #indexes in LValue

    def __repr__(self):
        return "Address " + str(self.n)

    def explain(self):
        return "combine the top " + str(self.n) + " values on the stack into an address and push the result"

    def eval(self, state, context):
        indexes = []
        for i in range(self.n):
            indexes = [context.pop()] + indexes;
        av = indexes[0]
        assert isinstance(av, AddressValue), av
        context.push(AddressValue(av.indexes + indexes[1:]))
        context.pc += 1

class StoreVarOp(Op):
    def __init__(self, v):
        self.v = v

    def __repr__(self):
        if self.v == None:
            return "StoreVar"
        else:
            (lexeme, file, line, column) = self.v
            return "StoreVar " + str(lexeme)

    def explain(self):
        if self.v == None:
            return "pop a value and the address of a method variable and store the value at that address"
        else:
            return "pop a value and store in method variable " + self.v[0]

    def eval(self, state, context):
        if self.v == None:
            value = context.pop()
            av = context.pop();
            assert isinstance(av, AddressValue)
            try:
                context.set(av.indexes, value)
                context.pc += 1
            except AttributeError:
                context.failure = "Error: " + str(av.indexes) + " not a dictionary"
        else:
            (lexeme, file, line, column) = self.v
            try:
                context.set([lexeme], context.pop())
                context.pc += 1
            except AttributeError:
                context.failure = "Error: " + str(self.v) + " not a dictionary"

class DelVarOp(Op):
    def __init__(self, v):
        self.v = v

    def __repr__(self):
        if self.v == None:
            return "DelVar"
        else:
            (lexeme, file, line, column) = self.v
            return "DelVar " + str(lexeme)

    def explain(self):
        if self.v == None:
            return "pop an address of a method variable and delete that variable"
        else:
            return "delete method variable " + self.v[0]

    def eval(self, state, context):
        if self.v == None:
            av = context.pop();
            assert isinstance(av, AddressValue)
            context.delete(av.indexes)
        else:
            (lexeme, file, line, column) = self.v
            context.delete([lexeme])
        context.pc += 1

class ChooseOp(Op):
    def __repr__(self):
        return "Choose"

    def explain(self):
        return "pop a set value and push one of its elements"

    def eval(self, state, context):
        v = context.pop()
        assert isinstance(v, SetValue), v
        assert len(v.s) == 1, v
        for e in v.s:
            context.push(e)
        context.pc += 1

class AssertOp(Op):
    def __init__(self, token, exprthere):
        self.token = token
        self.exprthere = exprthere

    def __repr__(self):
        return "Assert2" if self.exprthere else "Assert"

    def explain(self):
        if self.exprthere:
            return "pop a value and a condition and raise exception if condition is false"
        else:
            return "pop a condition and raise exception if condition is false"

    def eval(self, state, context):
        if self.exprthere:
            expr = context.pop()
        cond = context.pop()
        if not isinstance(cond, bool):
            context.failure = "Error: argument to " + str(self.token) + \
                        " must be a boolean: " + strValue(cond)
            return
        if not cond:
            context.failure = "Harmony Assertion failed "
            if self.exprthere:
                context.failure += ": " + strValue(expr)
            return
        context.pc += 1

class PopOp(Op):
    def __init__(self):
        pass

    def __repr__(self):
        return "Pop"

    def explain(self):
        return "discard the top value on the stack"

    def eval(self, state, context):
        context.pop()
        context.pc += 1

class FrameOp(Op):
    def __init__(self, name, args):
        self.name = name
        self.args = args

    def __repr__(self):
        (lexeme, file, line, column) = self.name
        args = ""
        for a in self.args:
            if args != "":
                args += ", "
            args += a[0]
        return "Frame " + str(lexeme) + "(" + str(args) + ")"

    def explain(self):
        return "start of method " + str(self.name[0])

    def eval(self, state, context):
        arg = context.pop()
        context.push(arg)               # restore for easier debugging
        context.push(context.vars)
        context.push(context.fp)
        context.fp = len(context.stack) # points to old fp, old vars, and return address
        if len(self.args) != 1:
            if (not isinstance(arg, DictValue)) or (len(self.args) != len(arg.d)):
                context.failure = "Error: argument count mismatch " + \
                        str(self.name) + ": expected " + str(len(self.args)) + \
                        " arguments but got " + \
                        str(len(arg.d) if isinstance(arg, DictValue) else 1)
                return
        if self.args == []:
            context.vars = DictValue({ "result": novalue })
        elif len(self.args) == 1:
            (lexeme, file, line, column) = self.args[0]
            context.vars = DictValue({ "result": novalue, lexeme: arg })
        else:
            context.vars = DictValue({ "result": novalue })
            for i in range(len(self.args)):
                (lexeme, file, line, column) = self.args[i]
                context.vars.d[lexeme] = arg.d[i]
        context.pc += 1

class ReturnOp(Op):
    def __repr__(self):
        return "Return"

    def explain(self):
        return "restore caller method state and push result"

    def eval(self, state, context):
        if len(context.stack) == 0:
            assert context.nametag == DictValue({"name": "__init__", "tag": novalue})
            context.terminated = True
            return
        result = context.get("result")
        context.fp = context.pop()
        context.vars = context.pop()
        context.pop()       # argument saved for debugging
        assert isinstance(context.vars, DictValue)
        calltype = context.pop()
        if calltype == "normal":
            pc = context.pop()
            assert isinstance(pc, PcValue)
            context.pc = pc.pc
            context.push(result)
        elif calltype == "interrupt":
            pc = context.pop()
            assert isinstance(pc, PcValue)
            context.pc = pc.pc
        elif calltype == "process":
            context.terminated = True
        else:
            assert False, calltype

class SpawnOp(Op):
    def __repr__(self):
        return "Spawn"

    def explain(self):
        return "pop a pc, argument, and tag and spawn a new process"

    def eval(self, state, context):
        method = context.pop()
        assert isinstance(method, PcValue)
        arg = context.pop()
        tag = context.pop()
        frame = state.code[method.pc]
        assert isinstance(frame, FrameOp)
        (lexeme, file, line, column) = frame.name
        ctx = ContextValue(DictValue({"name": lexeme, "tag": tag}), method.pc)
        ctx.push("process")
        ctx.push(arg)
        state.add(ctx)
        context.pc += 1

class TrapOp(Op):
    def __repr__(self):
        return "Trap"

    def explain(self):
        return "pop a pc and argument and set trap"

    def eval(self, state, context):
        method = context.pop()
        assert isinstance(method, PcValue)
        arg = context.pop()
        frame = state.code[method.pc]
        assert isinstance(frame, FrameOp)
        context.trap = (method, arg)
        context.interruptable = True
        context.pc += 1

class AtomicIncOp(Op):
    def __repr__(self):
        return "AtomicInc"

    def explain(self):
        return "increment atomic counter of context; process runs uninterrupted if larger than 0"

    def eval(self, state, context):
        context.atomic += 1
        context.pc += 1

class AtomicDecOp(Op):
    def __repr__(self):
        return "AtomicDec"

    def explain(self):
        return "decrement atomic counter of context"

    def eval(self, state, context):
        assert context.atomic > 0
        context.atomic -= 1
        context.pc += 1

class JumpOp(Op):
    def __init__(self, pc):
        self.pc = pc

    def __repr__(self):
        return "Jump " + str(self.pc)

    def explain(self):
        return "set program counter to " + str(self.pc)

    def eval(self, state, context):
        context.pc = self.pc

class JumpCondOp(Op):
    def __init__(self, cond, pc):
        self.cond = cond
        self.pc = pc

    def __repr__(self):
        return "JumpCond " + str(self.cond) + " " + str(self.pc)

    def explain(self):
        return "pop a value and jump to " + str(self.pc) + \
            " if the value is " + strValue(self.cond)

    def eval(self, state, context):
        c = context.pop()
        if c == self.cond:
            context.pc = self.pc
        else:
            context.pc += 1

class SetOp(Op):
    def __repr__(self):
        return "Set"

    def explain(self):
        return "pop a number n and n values and push a set with the value"

    def eval(self, state, context):
        nitems = context.pop()
        s = set()
        for i in range(nitems):
            s.add(context.pop())
        context.push(SetValue(s))
        context.pc += 1

class DictOp(Op):
    def __repr__(self):
        return "Dict"

    def explain(self):
        return "pop a number n and n key/value pairs and push a dictionary"

    def eval(self, state, context):
        nitems = context.pop()
        d = {}
        for i in range(nitems):
            k = context.pop()
            v = context.pop()
            d[k] = v
        context.push(DictValue(d))
        context.pc += 1

class NaryOp(Op):
    def __init__(self, op, n):
        self.op = op
        self.n = n

    def __repr__(self):
        (lexeme, file, line, column) = self.op
        return "%d-ary "%self.n + str(lexeme)

    def explain(self):
        return "pop " + str(self.n) + \
            (" value" if self.n == 1 else " values") + \
            " and push the result of applying " + self.op[0]

    def atLabel(self, state, label):
        pc = state.labels[label]
        d = {}
        for (ctx, cnt) in state.ctxbag.items():
            if ctx.pc == pc:
                c = d.get(ctx.nametag)
                d[ctx.nametag] = cnt if c == None else (c + cnt)
        return DictValue(d)

    def concat(self, d1, d2):
        result = []
        keys = sorted(d1.d.keys(), key=keyValue)
        for k in keys:
            result.append(d1.d[k])
        keys = sorted(d2.d.keys(), key=keyValue)
        for k in keys:
            result.append(d2.d[k])
        return DictValue({ i:result[i] for i in range(len(result)) })

    def checktype(self, state, args, chk):
        assert len(args) == self.n, (self, args)
        if not chk:
            context.failure = "Error: unexpected types in " + str(self.op) + \
                        " operands: " + str(list(reversed(args)))
            return False
        return True

    def eval(self, state, context):
        (op, file, line, column) = self.op
        assert len(context.stack) >= self.n
        sa = context.stack[-self.n:]
        if op in { "+", "*" }:
            assert self.n > 1
            e2 = context.pop()
            for i in range(1, self.n):
                e1 = context.pop()
                if op == "+":
                    if isinstance(e1, int):
                        if not self.checktype(state, sa, isinstance(e2, int)):
                            return
                        e2 += e1
                    elif isinstance(e1, SetValue):
                        if not self.checktype(state, sa, isinstance(e2, SetValue)):
                            return
                        e2 = SetValue(e2.s.union(e1.s))
                    else:
                        if not self.checktype(state, sa, isinstance(e1, DictValue)):
                            return
                        if not self.checktype(state, sa, isinstance(e2, DictValue)):
                            return
                        e2 = self.concat(e1, e2)
                elif op == "*":
                    if isinstance(e1, int):
                        if not self.checktype(state, sa, isinstance(e2, int)):
                            return
                        e2 *= e1
                    else:
                        if not self.checktype(state, sa, isinstance(e1, SetValue)):
                            return
                        if not self.checktype(state, sa, isinstance(e2, SetValue)):
                            return
                        e2 = SetValue(e2.s.intersection(e1.s))
                else:
                    assert False, op
            context.push(e2)
        elif self.n == 1:
            e = context.pop()
            if op == "-":
                if not self.checktype(state, sa, isinstance(e, int) or isinstance(e, float)):
                    return
                context.push(-e)
            elif op == "not":
                if not self.checktype(state, sa, isinstance(e, bool)):
                    return
                context.push(not e)
            elif op == "atLabel":
                if not context.atomic:
                    context.failure = "not in atomic block: " + str(self.op)
                    return
                if not self.checktype(state, sa, isinstance(e, str)):
                    return
                context.push(self.atLabel(state, e))
            elif op == "cardinality":
                if not self.checktype(state, sa, isinstance(e, SetValue)):
                    return
                context.push(len(e.s))
            elif op == "min":
                if not self.checktype(state, sa, isinstance(e, SetValue)):
                    return
                lst = sorted(e.s, key=keyValue)
                context.push(lst[0])
            elif op == "max":
                if not self.checktype(state, sa, isinstance(e, SetValue)):
                    return
                lst = sorted(e.s, key=keyValue)
                context.push(lst[-1])
            elif op == "nametag":
                if not self.checktype(state, sa, e == novalue):
                    return
                context.push(context.nametag)
            elif op == "processes":
                if not self.checktype(state, sa, e == novalue):
                    return
                if not context.atomic:
                    context.failure = "not in atomic block: " + str(self.op)
                    return
                d = {}
                for (ctx, cnt) in state.ctxbag.items():
                    c = d.get(ctx.nametag)
                    d[ctx.nametag] = cnt if c == None else (c + cnt)
                context.push(DictValue(d))
            elif op == "len":
                if not self.checktype(state, sa, isinstance(e, DictValue)):
                    return
                context.push(len(e.d))
            elif op == "keys":
                if not self.checktype(state, sa, isinstance(e, DictValue)):
                    return
                context.push(SetValue(set(e.d.keys())))
            elif op == "hash":
                context.push((e,).__hash__())
            elif op == "bagsize":
                if not self.checktype(state, sa, isinstance(e, DictValue)):
                    return
                context.push(sum(e.d.values()))
            else:
                assert False, self
        elif self.n == 2:
            e2 = context.pop()
            e1 = context.pop()
            if op == "==":
                # if not self.checktype(state, sa, type(e1) == type(e2)):
                #     return
                context.push(e1 == e2)
            elif op == "!=":
                # if not self.checktype(state, sa, type(e1) == type(e2)):
                #     return
                context.push(e1 != e2)
            elif op == "<":
                context.push(keyValue(e1) < keyValue(e2))
            elif op == "<=":
                context.push(keyValue(e1) <= keyValue(e2))
            elif op == ">":
                context.push(keyValue(e1) > keyValue(e2))
            elif op == ">=":
                context.push(keyValue(e1) >= keyValue(e2))
            elif op == "-":
                if isinstance(e1, int) or isinstance(e1, float):
                    if not self.checktype(state, sa, isinstance(e2, int) or isinstance(e2, float)):
                        return
                    context.push(e1 - e2)
                else:
                    if not self.checktype(state, sa, isinstance(e1, SetValue)):
                        return
                    if not self.checktype(state, sa, isinstance(e2, SetValue)):
                        return
                    context.push(SetValue(e1.s.difference(e2.s)))
            elif op == "/":
                if not self.checktype(state, sa, isinstance(e1, int) or isinstance(e1, float)):
                    return
                if not self.checktype(state, sa, isinstance(e2, int) or isinstance(e2, float)):
                    return
                if isinstance(e1, int) and (e2 == math.inf or e2 == -math.inf):
                    context.push(0)
                else:
                    context.push(e1 // e2)
            elif op == "%":
                if not self.checktype(state, sa, isinstance(e1, int)):
                    return
                if not self.checktype(state, sa, isinstance(e2, int)):
                    return
                context.push(e1 % e2)
            elif op == "..":
                if not self.checktype(state, sa, isinstance(e1, int)):
                    return
                if not self.checktype(state, sa, isinstance(e2, int)):
                    return
                context.push(SetValue(set(range(e1, e2+1))))
            elif op == "in":
                if not self.checktype(state, sa, isinstance(e2, SetValue)):
                    return
                context.push(e1 in e2.s)
            else:
                assert False, self
        else:
            assert False, self
        context.pc += 1

class ApplyOp(Op):
    def __init__(self, token):
        self.token = token

    def __repr__(self):
        return "Apply"

    def explain(self):
        return "pop a pc or dictionary f and an index i and push f(i)"

    def eval(self, state, context):
        method = context.pop()
        e = context.pop()
        if isinstance(method, DictValue):
            try:
                context.push(method.d[e])
            except KeyError:
                context.failure = "Error: no entry " + str(e) + " in " + \
                        str(self.token) + " = " + str(method)
                return
            context.pc += 1
        else:
            # TODO.  Need a token to have location
            if not isinstance(method, PcValue):
                context.failure = "pc = " + str(context.pc) + \
                    ": Error: must be either a method or a dictionary"
                return
            context.push(PcValue(context.pc + 1))
            context.push("normal")
            context.push(e)
            context.pc = method.pc

class AST:
    def isConstant(self, scope):
        return False

    def eval(self, scope, code):
        state = State(code, scope.labels)
        ctx = ContextValue(DictValue({"name": "__eval__", "tag": novalue}), 0)
        ctx.atomic = 1
        while ctx.pc != len(code) and ctx.failure == None:
            code[ctx.pc].eval(state, ctx)
        if ctx.failure != None:
            print("constant evaluation failed: ", self, ctx.failure)
            exit(1)
        return ctx.pop()

    def compile(self, scope, code):
        if self.isConstant(scope):
            code2 = []
            self.gencode(scope, code2)
            v = self.eval(scope, code2)
            code.append(PushOp((v, None, None, None)))
        else:
            self.gencode(scope, code)

class ConstantAST(AST):
    def __init__(self, const):
        self.const = const

    def __repr__(self):
        return str(self.const)

    def compile(self, scope, code):
        code.append(PushOp(self.const))

    def isConstant(self, scope):
        return True

class NameAST(AST):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return str(self.name)

    def compile(self, scope, code):
        (lexeme, file, line, column) = self.name
        tv = scope.lookup(self.name)
        if tv == None:
            code.append(LoadOp(self.name, self.name))
        else:
            (t, v) = tv
            if t == "variable":
                code.append(LoadVarOp(self.name))
            elif t == "constant":
                code.append(PushOp(v))
            else:
                assert False, tv

    def isConstant(self, scope):
        (lexeme, file, line, column) = self.name
        tv = scope.lookup(self.name)
        if tv == None:
            return False
        (t, v) = tv
        if t == "variable":
            return False
        elif t == "constant":
            return True
        else:
            assert False, tv

class SetAST(AST):
    def __init__(self, collection):
        self.collection = collection

    def __repr__(self):
        return str(self.collection)

    def isConstant(self, scope):
        return all(x.isConstant(scope) for x in self.collection)

    def gencode(self, scope, code):
        for e in self.collection:
            e.compile(scope, code)
        code.append(PushOp((len(self.collection), None, None, None)))
        code.append(SetOp())

class DictAST(AST):
    def __init__(self, record):
        self.record = record

    def __repr__(self):
        return str(self.record)

    def isConstant(self, scope):
        return all(k.isConstant(scope) and v.isConstant(scope)
                        for (k, v) in self.record.items())

    def gencode(self, scope, code):
        for (k, v) in self.record.items():
            v.compile(scope, code)
            k.compile(scope, code)
        code.append(PushOp((len(self.record), None, None, None)))
        code.append(DictOp())

class SetComprehensionAST(AST):
    def __init__(self, value, var, expr):
        self.value = value
        self.var = var
        self.expr = expr

    def __repr__(self):
        return "SetComprehension(" + str(self.var) + ")"

    def compile(self, scope, code):
        scope.checkUnused(self.var)
        uid = len(code)
        (var, file, line, column) = self.var

        # Evaluate the set and store in a temporary variable
        # TODO.  Should store as sorted list for determinism
        self.expr.compile(scope, code)
        S = ("%set:"+str(uid), file, line, column)
        code.append(StoreVarOp(S))

        # Also store the size
        N = ("%size:"+str(uid), file, line, column)
        code.append(LoadVarOp(S))
        code.append(NaryOp(("cardinality", file, line, column), 1))
        code.append(StoreVarOp(N))

        # Now generate the code:
        #   while X != {}:
        #       var := oneof X
        #       X := X - var
        #       push value
        pc = len(code)
        code.append(LoadVarOp(S))
        code.append(PushOp((SetValue(set()), file, line, column)))
        code.append(NaryOp(("!=", file, line, column), 2))
        tst = len(code)
        code.append(None)       # going to plug in a Jump op here
        code.append(LoadVarOp(S))
        code.append(CutOp())  
        code.append(StoreVarOp(S))
        code.append(StoreVarOp(self.var))

        # TODO.  Figure out how to do this better
        ns = Scope(scope)
        ns.names[var] = ("variable", self.var)

        self.value.compile(ns, code)
        code.append(JumpOp(pc))
        code[tst] = JumpCondOp(False, len(code))
        code.append(LoadVarOp(N))
        code.append(SetOp())

        code.append(DelVarOp(self.var))
        code.append(DelVarOp(S))
        code.append(DelVarOp(N))

class DictComprehensionAST(AST):
    def __init__(self, value, var, expr):
        self.value = value
        self.var = var
        self.expr = expr

    def __repr__(self):
        return "DictComprehension(" + str(self.var) + ")"

    def compile(self, scope, code):
        scope.checkUnused(self.var)
        uid = len(code)
        (var, file, line, column) = self.var

        # Evaluate the set and store in a temporary variable
        # TODO.  Should store as sorted list for determinism
        self.expr.compile(scope, code)
        S = ("%set:"+str(uid), file, line, column)
        code.append(StoreVarOp(S))

        # Also store the size
        N = ("%size:"+str(uid), file, line, column)
        code.append(LoadVarOp(S))
        code.append(NaryOp(("cardinality", file, line, column), 1))
        code.append(StoreVarOp(N))

        # Now generate the code:
        #   while X != {}:
        #       var := oneof X
        #       X := X - var
        #       push value
        #       push key
        pc = len(code)
        code.append(LoadVarOp(S))
        code.append(PushOp((SetValue(set()), file, line, column)))
        code.append(NaryOp(("!=", file, line, column), 2))
        tst = len(code)
        code.append(None)       # going to plug in a Jump op here
        code.append(LoadVarOp(S))
        code.append(CutOp())  
        code.append(StoreVarOp(S))
        code.append(StoreVarOp(self.var))

        # TODO.  Figure out how to do this better
        ns = Scope(scope)
        ns.names[var] = ("variable", self.var)

        self.value.compile(ns, code)
        code.append(LoadVarOp(self.var))
        code.append(JumpOp(pc))
        code[tst] = JumpCondOp(False, len(code))
        code.append(LoadVarOp(N))
        code.append(DictOp())

        code.append(DelVarOp(self.var))
        code.append(DelVarOp(S))
        code.append(DelVarOp(N))

class ListComprehensionAST(AST):
    def __init__(self, value, var, expr):
        self.value = value
        self.var = var
        self.expr = expr

    def __repr__(self):
        return "ListComprehension(" + str(self.var) + ")"

    def compile(self, scope, code):
        scope.checkUnused(self.var)
        uid = len(code)
        (var, file, line, column) = self.var

        # Evaluate the set and store in a temporary variable
        # TODO.  Should store as sorted list for determinism
        self.expr.compile(scope, code)
        S = ("%set:"+str(uid), file, line, column)
        code.append(StoreVarOp(S))

        # Also store the size
        N = ("%size:"+str(uid), file, line, column)
        code.append(LoadVarOp(S))
        code.append(NaryOp(("cardinality", file, line, column), 1))
        code.append(StoreVarOp(N))

        # Create an index variable, initialized to 0
        code.append(PushOp((0, file, line, column)))
        I = ("%index:"+str(uid), file, line, column)
        code.append(StoreVarOp(I))

        # Now generate the code:
        #   while X != {}:
        #       var := oneof X
        #       X := X - var
        #       push value
        #       push index
        #       increment index
        pc = len(code)
        code.append(LoadVarOp(S))
        code.append(PushOp((SetValue(set()), file, line, column)))
        code.append(NaryOp(("!=", file, line, column), 2))
        tst = len(code)
        code.append(None)       # going to plug in a Jump op here
        code.append(LoadVarOp(S))
        code.append(CutOp())  
        code.append(StoreVarOp(S))
        code.append(StoreVarOp(self.var))

        # TODO.  Figure out how to do this better
        ns = Scope(scope)
        ns.names[var] = ("variable", self.var)

        # push value and index
        self.value.compile(ns, code)
        code.append(LoadVarOp(I))

        # increment index
        code.append(PushOp((1, file, line, column)))
        code.append(LoadVarOp(I))
        code.append(NaryOp(("+", file, line, column), 2))
        code.append(StoreVarOp(I))

        code.append(JumpOp(pc))
        code[tst] = JumpCondOp(False, len(code))
        code.append(LoadVarOp(N))
        code.append(DictOp())

        code.append(DelVarOp(self.var))
        code.append(DelVarOp(S))
        code.append(DelVarOp(N))
        code.append(DelVarOp(I))

# N-ary operator
class NaryAST(AST):
    def __init__(self, op, args):
        self.op = op
        self.args = args
        assert all(isinstance(x, AST) for x in args)

    def __repr__(self):
        return "NaryOp(" + str(self.op) + ", " + str(self.args) + ")"

    def isConstant(self, scope):
        (op, file, line, column) = self.op
        if op in { "atLabel", "choose", "nametag", "processes" }:
            return False
        return all(x.isConstant(scope) for x in self.args)

    def gencode(self, scope, code):
        (op, file, line, column) = self.op
        n = len(self.args)
        if op == "and" or op == "or":
            self.args[0].compile(scope, code)
            pcs = []
            for i in range(1, n):
                pcs.append(len(code))
                code.append(None)
                self.args[i].compile(scope, code)
            code.append(JumpOp(len(code) + 2))
            for pc in pcs:
                code[pc] = JumpCondOp(op == "or", len(code))
            code.append(PushOp((op == "or", file, line, column)))
        elif op == "if":
            assert n == 3, n
            self.args[1].compile(scope, code)
            pc1 = len(code)
            code.append(None)
            self.args[0].compile(scope, code)
            pc2 = len(code)
            code.append(None)
            self.args[2].compile(scope, code)
            code[pc1] = JumpCondOp(False, pc2 + 1)
            code[pc2] = JumpOp(len(code))
        elif op == "choose":
            assert n == 1
            self.args[0].compile(scope, code)
            code.append(ChooseOp())
        else:
            for i in range(n):
                self.args[i].compile(scope, code)
            code.append(NaryOp(self.op, n))

class ApplyAST(AST):
    def __init__(self, method, arg, token):
        self.method = method
        self.arg = arg
        self.token = token

    def __repr__(self):
        return "Apply(" + str(self.method) + ", " + str(self.arg) + ")"

    def compile(self, scope, code):
        self.arg.compile(scope, code)
        self.method.compile(scope, code)
        code.append(ApplyOp(self.token))

class Rule:
    def expect(self, rule, b, got, want):
        if not b:
            print("Parse error in %s."%rule, "Got", got, ":", want)
            exit(1)

class NaryRule(Rule):
    def __init__(self, closers):
        self.closers = closers

    def parse(self, t):
        (ast, t) = ExpressionRule().parse(t)
        (lexeme, file, line, column) = t[0]
        if lexeme in self.closers:
            return (ast, t)
        args = [ast]
        op = t[0]
        invert = None
        if op[0] == "not":
            invert = op
            t = t[1:]
            op = t[0]
        self.expect("n-ary operation", isbinaryop(op[0]) or op[0] == "if", op,
                    "expected binary operation or 'if'")
        (ast2, t) = ExpressionRule().parse(t[1:])
        if ast2 == False:
            print("expected an expression after binary operation", op)
            exit(1)
        args.append(ast2)
        (lexeme, file, line, column) = t[0]
        if op[0] == "if":
            self.expect("n-ary operation", lexeme == "else", t[0], "expected 'else'")
            (ast3, t) = ExpressionRule().parse(t[1:])
            if ast3 == False:
                print("expected an expression after else in", op)
                exit(1)
            args.append(ast3)
            (lexeme, file, line, column) = t[0]
        elif (op[0] == lexeme) and (lexeme in { "+", "*", "and", "or" }):
            while op[0] == lexeme:
                (ast3, t) = ExpressionRule().parse(t[1:])
                if ast3 == False:
                    print("expected an expression after n-ary operation in", op)
                    exit(1)
                args.append(ast3)
                (lexeme, file, line, column) = t[0]
        self.expect("n-ary operation", lexeme in self.closers, t[0],
                            "expected one of %s"%self.closers)
        ast = NaryAST(op, args)
        if invert != None:
            return (NaryAST(invert, [ast]), t)
        else:
            return (ast, t)

class SetComprehensionRule(Rule):
    def __init__(self, value):
        self.value = value

    def parse(self, t):
        name = t[0]
        (lexeme, file, line, column) = name
        self.expect("set comprehension", isname(lexeme), name, "expected a name")
        (lexeme, file, line, column) = t[1]
        self.expect("set comprehension", lexeme == "in", t[1], "expected 'in'")
        (expr, t) = NaryRule({"}"}).parse(t[2:])
        return (SetComprehensionAST(self.value, name, expr), t[1:])

class DictComprehensionRule(Rule):
    def __init__(self, value):
        self.value = value

    def parse(self, t):
        name = t[0]
        (lexeme, file, line, column) = name
        self.expect("dict comprehension", isname(lexeme), name, "expected a name")
        (lexeme, file, line, column) = t[1]
        self.expect("dict comprehension", lexeme == "in", t[1], "expected 'in'")
        (expr, t) = NaryRule({"}"}).parse(t[2:])
        return (DictComprehensionAST(self.value, name, expr), t[1:])

class ListComprehensionRule(Rule):
    def __init__(self, ast, closer):
        self.ast = ast
        self.closer = closer

    def parse(self, t):
        name = t[0]
        (lexeme, file, line, column) = name
        self.expect("list comprehension", isname(lexeme), name, "expected a name")
        (lexeme, file, line, column) = t[1]
        self.expect("list comprehension", lexeme == "in", t[1], "expected 'in'")
        (expr, t) = NaryRule({"]"}).parse(t[2:])
        return (ListComprehensionAST(self.ast, name, expr), t[1:])

class SetRule(Rule):
    def parse(self, t):
        (lexeme, file, line, column) = t[0]
        self.expect("set expression", lexeme == "{", t[0], "expected '{'")
        (lexeme, file, line, column) = t[1]
        if lexeme == "}":
            return (SetAST([]), t[2:])
        s = []
        while True:
            (next, t) = NaryRule({"for", ",", "}"}).parse(t[1:])
            if next == False:
                return (next, t)
            s.append(next)
            (lexeme, file, line, column) = t[0]
            if lexeme == "for":
                self.expect("set comprehension", len(s) == 1, t[0],
                    "can have only one expression")
                return SetComprehensionRule(s[0]).parse(t[1:])
            if lexeme == "}":
                return (SetAST(s), t[1:])
            self.expect("set expression", lexeme == ",", t[0],
                    "expected a comma")

class DictRule(Rule):
    def parse(self, t):
        (lexeme, file, line, column) = t[0]
        self.expect("dict expression", lexeme == "dict{", t[0],
                "expected dict{")
        (lexeme, file, line, column) = t[1]
        if lexeme == "}":
            return (DictAST({}), t[2:])
        d = {}
        while lexeme != "}":
            (key, t) = NaryRule({":", "for"}).parse(t[1:])
            if key == False:
                return (key, t)
            (lexeme, file, line, column) = t[0]
            if lexeme == "for":
                self.expect("dict comprehension", d == {}, t[0],
                    "expected single expression")
                return DictComprehensionRule(key).parse(t[1:])
            self.expect("dict expression", lexeme == ":", t[0],
                                        "expected a colon")
            (value, t) = NaryRule({",", "}"}).parse(t[1:])
            (lexeme, file, line, column) = t[0]
            self.expect("dict expression", lexeme in { ",", "}" }, t[0],
                                    "expected a comma or '}'")
            d[key] = value
        return (DictAST(d), t[1:])

class TupleRule(Rule):
    def __init__(self, ast, closer):
        self.ast = ast
        self.closer = closer

    def parse(self, t):
        (lexeme, file, line, column) = t[0]
        if lexeme == "for":
            return ListComprehensionRule(self.ast, self.closer).parse(t[1:])
        d = { ConstantAST((0, file, line, column)): self.ast }
        i = 1
        while lexeme == ",":
            (lexeme, file, line, column) = t[1]
            if lexeme == self.closer:
                return (DictAST(d), t[2:])
            (next, t) = NaryRule({ self.closer, "," }).parse(t[1:])
            d[ConstantAST((i, file, line, column))] = next
            i += 1
            (lexeme, file, line, column) = t[0]
        self.expect("dict expression", lexeme == self.closer, t[0],
                "expected %s"%self.closer)
        return (DictAST(d), t[1:])

class BasicExpressionRule(Rule):
    def parse(self, t):
        (lexeme, file, line, column) = t[0]
        if isnumber(lexeme):
            return (ConstantAST((int(lexeme), file, line, column)), t[1:])
        if lexeme == "False":
            return (ConstantAST((False, file, line, column)), t[1:])
        if lexeme == "True":
            return (ConstantAST((True, file, line, column)), t[1:])
        if lexeme == "None":
            return (ConstantAST((AddressValue([]), file, line, column)), t[1:])
        if lexeme == "inf":
            return (ConstantAST((math.inf, file, line, column)), t[1:])
        if lexeme[0] == '"':
            return (DictAST({ ConstantAST((i-1, file, line, column)):
                        ConstantAST((lexeme[i:i+1], file, line, column))
                                for i in range(1, len(lexeme) - 1) }), t[1:])
        if lexeme == ".": 
            (lexeme, file, line, column) = t[1]
            self.expect("dot expression", isname(lexeme), t[1],
                    "expected a name after .")
            return (ConstantAST((lexeme, file, line, column)), t[2:])
        if isname(lexeme):
            return (NameAST(t[0]), t[1:])
        if lexeme == "{":
            return SetRule().parse(t)
        if lexeme == "dict{":
            return DictRule().parse(t)
        if lexeme == "(" or lexeme == "[":
            closer = ")" if lexeme == "(" else "]"
            (lexeme, file, line, column) = t[1]
            if lexeme == closer:
                return (ConstantAST(
                    (novalue, file, line, column)), t[2:])
            (ast, t) = NaryRule({closer, ",", "for"}).parse(t[1:])
            if not ast:
                return (ast, t)
            (lexeme, file, line, column) = t[0]
            if lexeme != closer:
                return TupleRule(ast, closer).parse(t)
            else:
                return (ast, t[1:])
        if lexeme == "&":
            (ast, t) = LValueRule().parse(t[1:])
            return (AddressAST(ast), t)
        return (False, t)

class LValueAST(AST):
    def __init__(self, indexes, token):
        self.indexes = indexes
        self.token = token  # for error messages

    def __repr__(self):
        return "LValueAST(" + str(self.indexes) + " " + str(self.token) + ")"

class PointerAST(AST):
    def __init__(self, expr, token):
        self.expr = expr
        self.token = token

    def __repr__(self):
        return "Pointer(" + str(self.expr) + ")"

    def compile(self, scope, code):
        self.expr.compile(scope, code)
        code.append(LoadOp(None, self.token))

class ExpressionRule(Rule):
    def parse(self, t):
        func = t[0]
        (lexeme, file, line, column) = func
        if lexeme == "stop":
            (ast, t) = LValueRule().parse(t[1:])
            return (StopAST(ast), t)
        if isunaryop(lexeme):
            (ast, t) = ExpressionRule().parse(t[1:])
            if lexeme == "^":
                return (PointerAST(ast, func), t)
            else:
                return (NaryAST(func, [ast]), t)
        (ast, t) = BasicExpressionRule().parse(t)
        args = []
        while t != []:
            (arg, t) = BasicExpressionRule().parse(t)
            if arg == False:
                break
            args.append(arg)
        if ast == None:
            assert len(args) > 0, args
            ast = PointerAST(args[0], func)
            args = args[1:]
        while args != []:
            ast = ApplyAST(ast, args[0], func)
            args = args[1:]
        return (ast, t)

class AssignmentAST(AST):
    def __init__(self, lvs, rv, op):
        self.lvs = lvs       # list of LValues, for "x, y = ..." expressions
        self.rv = rv
        self.op = op

    def __repr__(self):
        return "Assign(" + str(self.lvs) + ", " + str(self.rv) + \
                            ", " + self.op + ")"

    def phase1(self, lv, scope, code):
        base = lv.indexes[0]
        n = len(lv.indexes)
        if isinstance(base, NameAST):
            tv = scope.lookup(base.name)
            if tv == None:
                if n > 1:
                    code.append(PushAddressOp(base.name))
                    for i in range(1, n):
                        lv.indexes[i].compile(scope, code)
                    code.append(AddressOp(n))
                if self.op[0] != "=":
                    if n > 1:
                        code.append(DupOp())
                        code.append(LoadOp(None, base.name))
                    else:
                        code.append(LoadOp(base.name, base.name))
            else:
                (t, v) = tv
                if t == "variable":
                    if n > 1:
                        code.append(PushAddressOp(v))
                        for i in range(1, n):
                            lv.indexes[i].compile(scope, code)
                        code.append(AddressOp(n))
                    if self.op[0] != "=":
                        if n > 1:
                            code.append(DupOp())
                            code.append(LoadVarOp(None))
                        else:
                            code.append(LoadVarOp(v))
                else:
                    assert False, tv
        else:
            assert isinstance(base, PointerAST), base
            base.expr.compile(scope, code)
            if n > 1:
                for i in range(1, n):
                    lv.indexes[i].compile(scope, code)
                code.append(AddressOp(n))
            if self.op[0] != "=":
                code.append(DupOp())
                code.append(LoadOp(None, base.token))

    def phase2(self, lv, scope, code, skip):
        if self.op[0] != "=":
            if skip > 0:
                code.append(MoveOp(skip + 2))
                code.append(MoveOp(2))
            code.append(NaryOp(self.op, 2))

        base = lv.indexes[0]
        n = len(lv.indexes)
        if isinstance(base, NameAST):
            tv = scope.lookup(base.name)
            if tv == None:
                if n > 1:
                    if skip > 0:
                        code.append(MoveOp(skip + 2))
                        code.append(MoveOp(2))
                    code.append(StoreOp(None, base.name))
                else:
                    code.append(StoreOp(base.name, base.name))
            else:
                (t, v) = tv
                if t == "variable":
                    if n > 1:
                        if skip > 0:
                            code.append(MoveOp(skip + 2))
                            code.append(MoveOp(2))
                        code.append(StoreVarOp(None))
                    else:
                        code.append(StoreVarOp(v))
                else:
                    assert False, tv
        else:
            assert isinstance(base, PointerAST), base
            if skip > 0:
                code.append(MoveOp(skip + 2))
                code.append(MoveOp(2))
            code.append(StoreOp(None, base.token))

    def compile(self, scope, code):
        assert all(isinstance(lv, LValueAST) for lv in self.lvs)
        if len(self.lvs) > 1:
            if isinstance(self.rv, DictAST):
                if len(self.rv.record) != len(self.lvs):
                    print("assignment lhs/rhs count mismatch", [x.token for x in self.lvs])
                    exit(1)

        for lv in self.lvs:
            self.phase1(lv, scope, code)
        self.rv.compile(scope, code)
        n = len(self.lvs)
        if n > 1:
            code.append(SplitOp())
        for lv in reversed(self.lvs):
            n -= 1
            self.phase2(lv, scope, code, n)

class DelAST(AST):
    def __init__(self, lv):
        self.lv = lv

    def __repr__(self):
        return "Del(" + str(self.lv) + ")"

    def compile(self, scope, code):
        assert isinstance(self.lv, LValueAST)
        n = len(self.lv.indexes)
        lv = self.lv.indexes[0]
        if isinstance(lv, NameAST):
            tv = scope.lookup(lv.name)
            if tv == None:
                if n > 1:
                    code.append(PushAddressOp(lv.name))
                    for i in range(1, n):
                        self.lv.indexes[i].compile(scope, code)
                    code.append(AddressOp(n))
                    code.append(DelOp(None))
                else:
                    code.append(DelOp(lv.name))
            else:
                (t, v) = tv
                if t == "variable":
                    if n > 1:
                        code.append(PushAddressOp(v))
                        for i in range(1, n):
                            self.lv.indexes[i].compile(scope, code)
                        code.append(AddressOp(n))
                        code.append(DelVarOp(None))
                    else:
                        code.append(DelVarOp(v))
                else:
                    assert False, tv
        else:
            lv.expr.compile(scope, code)
            if n > 1:
                for i in range(1, n):
                    self.lv.indexes[i].compile(scope, code)
                code.append(AddressOp(n))
            code.append(DelOp(None))

class StopAST(AST):
    def __init__(self, lv):
        self.lv = lv

    def __repr__(self):
        return "Stop " + str(self.lv)

    def compile(self, scope, code):
        assert isinstance(self.lv, LValueAST)
        n = len(self.lv.indexes)
        lv = self.lv.indexes[0]
        if isinstance(lv, NameAST):
            tv = scope.lookup(lv.name)
            if tv == None:
                if n > 1:
                    for i in range(1, n):
                        self.lv.indexes[n - i].compile(scope, code)
                    code.append(PushAddressOp(lv.name))
                    code.append(StopOp(None))
                else:
                    code.append(StopOp(lv.name))
                code.append(ContinueOp())
            else:
                print("Error: Can't store state in process variable")
                exit(1)
        else:
            assert isinstance(lv, PointerAST), lv
            lv.expr.compile(scope, code)
            if n > 1:
                for i in range(1, n):
                    self.lv.indexes[i].compile(scope, code)
                code.append(AddressOp(n))
            code.append(StopOp(None))
            code.append(ContinueOp())

class AddressAST(AST):
    def __init__(self, lv):
        self.lv = lv

    def __repr__(self):
        return "Address(" + str(self.lv) + ")"

    def isConstant(self, scope):
        lv = self.lv.indexes[0]
        if not isinstance(lv, NameAST):
            return False
        return all(x.isConstant(scope) for x in self.lv.indexes[1:])

    def gencode(self, scope, code):
        n = len(self.lv.indexes)
        lv = self.lv.indexes[0]
        if isinstance(lv, NameAST):
            tv = scope.lookup(lv.name)
            if tv != None:
                print(lv, ": Parse error: can only take address of shared variable")
                exit(1)
            code.append(PushAddressOp(lv.name))
        else:
            assert isinstance(lv, PointerAST), lv
            lv.expr.compile(scope, code)
        if n > 1:
            for i in range(1, n):
                self.lv.indexes[i].compile(scope, code)
            code.append(AddressOp(n))

class PassAST(AST):
    def __repr__(self):
        return "Pass"

    def compile(self, scope, code):
        pass

class BlockAST(AST):
    def __init__(self, b):
        self.b = b

    def __repr__(self):
        return "BlockRule(" + str(self.b) + ")"

    def compile(self, scope, code):
        for s in self.b:
            s.compile(scope, code)

class IfAST(AST):
    def __init__(self, alts, stat):
        self.alts = alts        # alternatives
        self.stat = stat        # else statement

    def __repr__(self):
        return "If(" + str(self.alts) + ", " + str(self.what) + ")"

    def compile(self, scope, code):
        jumps = []
        for alt in self.alts:
            (cond, stat) = alt
            cond.compile(scope, code)
            pc = len(code)
            code.append(None)
            stat.compile(scope, code)
            jumps += [len(code)]
            code.append(None)
            code[pc] = JumpCondOp(False, len(code))
        if self.stat != None:
            self.stat.compile(scope, code)
        for pc in jumps:
            code[pc] = JumpOp(len(code))

class WhileAST(AST):
    def __init__(self, cond, stat):
        self.cond = cond
        self.stat = stat

    def __repr__(self):
        return "While(" + str(self.cond) + ", " + str(self.stat) + ")"

    def compile(self, scope, code):
        pc1 = len(code)
        self.cond.compile(scope, code)
        pc2 = len(code)
        code.append(None)
        self.stat.compile(scope, code)
        code.append(JumpOp(pc1))
        code[pc2] = JumpCondOp(False, len(code))

class LetAST(AST):
    def __init__(self, vars, stat):
        self.vars = vars
        self.stat = stat

    def __repr__(self):
        return "Let(" + str(self.vars) + ", " + str(self.stat) + ")"

    def assign(self, scope, code, var):
        (type, v) = var;
        if type == "name":
            scope.checkUnused(v)
            (lexeme, file, line, column) = v
            scope.names[lexeme] = ("variable", v)
            code.append(StoreVarOp(v))
        else:
            assert type == "nest"
            assert len(v) > 0
            for index in range(0, len(v)):
                code.append(DupOp())
                code.append(PushOp((index, None, None, None)))      # TODO: file, line, col
                code.append(MoveOp(2))      # swap the top two elements on the stack
                code.append(ApplyOp(None))
                self.assign(scope, code, v[index])
            code.append(PopOp())        # TODO: last value does not need dupping

    def delete(self, scope, code, var):
        (type, v) = var;
        if type == "name":
            code.append(DelVarOp(v))  # remove variable
            (lexeme, file, line, column) = v
            del scope.names[lexeme]
        else:
            assert type == "nest"
            assert len(v) > 0
            for x in v:
                self.delete(scope, code, x)

    def compile(self, scope, code):
        for (var, expr) in self.vars:
            expr.compile(scope, code)
            self.assign(scope, code, var)

        # Run the body
        self.stat.compile(scope, code)

        # Restore the old variable state
        for (var, expr) in self.vars:
            self.delete(scope, code, var)

class ForAST(AST):
    def __init__(self, var, expr, stat):
        self.var = var
        self.expr = expr
        self.stat = stat

    def __repr__(self):
        return "For(" + str(self.var) + ", " + str(self.expr) + ", " + str(self.stat) + ")"

    def compile(self, scope, code):
        scope.checkUnused(self.var)
        uid = len(code)
        (var, file, line, column) = self.var

        self.expr.compile(scope, code)     # first push the set
        S = ("%set:"+str(uid), file, line, column)   # save in variable "%set"
        code.append(StoreVarOp(S))

        pc = len(code)      # top of loop
        code.append(LoadVarOp(S))
        code.append(PushOp((SetValue(set()), file, line, column)))
        code.append(NaryOp(("!=", file, line, column), 2))
        tst = len(code)
        code.append(None)       # going to plug in a Jump op here
        code.append(LoadVarOp(S))
        code.append(CutOp())  
        code.append(StoreVarOp(S))
        code.append(StoreVarOp(self.var))

        # TODO.  Figure out how to do this better
        ns = Scope(scope)
        ns.names[var] = ("variable", self.var)

        self.stat.compile(ns, code)
        code.append(JumpOp(pc))
        code[tst] = JumpCondOp(False, len(code))

        code.append(DelVarOp(self.var))
        code.append(DelVarOp(S))

class AtomicAST(AST):
    def __init__(self, stat):
        self.stat = stat

    def __repr__(self):
        return "Atomic(" + str(self.stat) + ")"

    def compile(self, scope, code):
        code.append(AtomicIncOp())
        self.stat.compile(scope, code)
        code.append(AtomicDecOp())

class AssertAST(AST):
    def __init__(self, token, cond, expr):
        self.token = token
        self.cond = cond
        self.expr = expr

    def __repr__(self):
        return "Assert(" + str(self.token) + str(self.cond) + ", " + str(self.expr) + ")"

    def compile(self, scope, code):
        code.append(AtomicIncOp())
        self.cond.compile(scope, code)
        if self.expr != None:
            self.expr.compile(scope, code)
        code.append(AssertOp(self.token, self.expr != None))
        code.append(AtomicDecOp())

class MethodAST(AST):
    def __init__(self, name, args, stat, fun):
        self.name = name
        self.args = args
        self.stat = stat
        self.fun = fun          # TODO.  Make atomic

    def __repr__(self):
        return "Method(" + str(self.name) + ", " + str(self.args) + ", " + str(self.stat) + ")"

    def compile(self, scope, code):
        pc = len(code)
        code.append(None)       # going to plug in a Jump op here
        code.append(FrameOp(self.name, self.args))
        (lexeme, file, line, column) = self.name
        scope.names[lexeme] = ("constant", (PcValue(pc + 1), file, line, column))

        ns = Scope(scope)
        for arg in self.args:
            (lexeme, afile, aline, acolumn) = arg
            ns.names[lexeme] = ("variable", arg)
        ns.names["result"] = ("variable", ("result", file, line, column))
        self.stat.compile(ns, code)
        code.append(ReturnOp())

        code[pc] = JumpOp(len(code))

class CallAST(AST):
    def __init__(self, expr):
        self.expr = expr

    def __repr__(self):
        return "Call(" + str(self.expr) + ")"

    def compile(self, scope, code):
        self.expr.compile(scope, code)
        code.append(PopOp())

class SpawnAST(AST):
    def __init__(self, tag, method, arg):
        self.tag = tag
        self.method = method
        self.arg = arg

    def __repr__(self):
        return "Spawn(" + str(self.tag) + ", " + str(self.method) + ", " + str(self.arg) + ")"

    def compile(self, scope, code):
        if self.tag != None:
            self.tag.compile(scope, code)
        self.arg.compile(scope, code)
        if self.tag == None:
            code.append(DupOp())
        self.method.compile(scope, code)
        code.append(SpawnOp())

class TrapAST(AST):
    def __init__(self, method, arg):
        self.method = method
        self.arg = arg

    def __repr__(self):
        return "Trap(" + str(self.method) + ", " + str(self.arg) + ")"

    def compile(self, scope, code):
        self.arg.compile(scope, code)
        self.method.compile(scope, code)
        code.append(TrapOp())

class GoAST(AST):
    def __init__(self, ctx, result):
        self.ctx = ctx
        self.result = result

    def __repr__(self):
        return "Spawn(" + str(self.tag) + ", " + str(self.ctx) + ", " + str(self.result) + ")"

    def compile(self, scope, code):
        self.result.compile(scope, code)
        self.ctx.compile(scope, code)
        code.append(GoOp())

class ImportAST(AST):
    def __init__(self, module):
        self.module = module

    def __repr__(self):
        return "Import(" + str(self.module) + ")"

    def compile(self, scope, code):
        (lexeme, file, line, column) = self.module
        if lexeme in modules:
            lexeme = modules[lexeme]
        for dir in [ os.path.dirname(namestack[-1]), "modules", "." ]:
            filename = dir + "/" + lexeme + ".hny"
            if os.path.exists(filename):
                with open(filename) as f:
                    load(f, filename, scope, code)
                return
        if lexeme in internal_modules:
            load_string(internal_modules[lexeme],
                "<internal>/" + lexeme + ".hny", scope, code)
        else:
            print("Can't find module", lexeme, "imported from", namestack)
            exit(1)

class LabelStatAST(AST):
    def __init__(self, labels, ast, file, line):
        self.labels = labels
        self.ast = ast
        self.file = file
        self.line = line

    def __repr__(self):
        return "LabelStat(" + str(self.labels) + ", " + str(self.ast) + ")"

    def compile(self, scope, code):
        scope.location(len(code), self.file, self.line, self.labels)
        if self.labels == []:
            self.ast.compile(scope, code)
        else:
            code.append(AtomicIncOp())
            self.ast.compile(scope, code)
            code.append(AtomicDecOp())

class ConstAST(AST):
    def __init__(self, const, expr):
        self.const = const
        self.expr = expr

    def __repr__(self):
        return "Const(" + str(self.const) + ", " + str(self.expr) + ")"

    def compile(self, scope, code):
        if not self.expr.isConstant(scope):
            print(self.const, ": Parse error: expression not a constant", str(self.expr))
            exit(1)
        code2 = []
        self.expr.compile(scope, code2)
        state = State(code2, scope.labels)
        ctx = ContextValue(DictValue({"name": "__const__", "tag": novalue}), 0)
        ctx.atomic = 1
        while ctx.pc != len(code2):
            code2[ctx.pc].eval(state, ctx)
        v = ctx.pop()
        (lexeme, file, line, column) = self.const
        if lexeme in constants:
            value = constants[lexeme]
        else:
            value = v
        scope.names[lexeme] = ("constant", (value, file, line, column))

class LValueRule(Rule):
    def parse(self, t):
        token = t[0]
        (lexeme, file, line, column) = token
        if lexeme == "^":
            (ast, t) = ExpressionRule().parse(t[1:])
            indexes = [PointerAST(ast, token)]
        elif lexeme == "(":
            (ast, t) = LValueRule().parse(t[1:])
            (lexeme, file, line, column) = t[0]
            self.expect("lvalue expression", lexeme == ")", t[0], "expected ')'")
            indexes = ast.indexes
            t = t[1:]
        elif lexeme == "stop":
            (ast, t) = ExpressionRule().parse(t)
            indexes = [ast]
        else:
            self.expect("lvalue expression", isname(lexeme), t[0],
                                "expecting a name")
            indexes = [NameAST(t[0])]
            t = t[1:]
        while t != []:
            (index, t) = BasicExpressionRule().parse(t)
            if index == False:
                break
            indexes.append(index)
        return (LValueAST(indexes, token), t)

class AssignmentRule(Rule):
    def __init__(self, lvs, op):
        self.lvs = lvs
        self.op = op

    def parse(self, t):
        (rv, t) = NaryRule({";", ","}).parse(t)
        (lexeme, file, line, column) = t[0]
        if lexeme == ",":
            (ast, t) = TupleRule(rv, ";").parse(t)
            return (AssignmentAST(self.lvs, ast, self.op), t)
        assert lexeme == ";", t
        return (AssignmentAST(self.lvs, rv, self.op), t[1:])

# Zero or more labels, then a statement, then a semicolon
class LabelStatRule(Rule):
    def parse(self, t):
        (lexeme, thefile, theline, column) = t[0]
        labels = []
        while True:
            (lexeme, file, line, column) = t[0]
            if lexeme != "@":
                break
            label = t[1]
            (lexeme, file, line, column) = label
            self.expect("label", isname(lexeme), t[1], "expected name after @")
            labels.append(label)
            (lexeme, file, line, column) = t[2]
            self.expect("label", lexeme == ":", t[2], "expected ':' after label")
            t = t[3:]

        (ast, t) = StatementRule().parse(t)
        return (LabelStatAST(labels, ast, thefile, theline), t)

class StatListRule(Rule):
    def __init__(self, delim):
        self.delim = delim

    def parse(self, t):
        b = []
        (lexeme, file, line, column) = t[0]
        while lexeme not in self.delim:
            (ast, t) = LabelStatRule().parse(t)
            b.append(ast)
            if t == [] and self.delim == set():
                break
            (lexeme, file, line, column) = t[0]
        return (BlockAST(b), t)

class BlockRule(Rule):
    def __init__(self, delim):
        self.delim = delim

    def parse(self, t):
        (lexeme, file, line, column) = t[0]
        self.expect("block statement", lexeme == ":", t[0], "missing ':'")
        return StatListRule(self.delim).parse(t[1:])

# This parses the lefthand side of an assignment in a let expression.  Grammar:
#   lhs = (tuple ",")* [tuple]
#   tuple = name | "(" lhs ")"
class LetLhsRule(Rule):
    def parse(self, t):
        tuples = []
        while True:
            (lexeme, file, line, column) = t[0]
            if (isname(lexeme)):
                tuples.append(("name", t[0]))
            elif lexeme == "(":
                (nest, t) = LetLhsRule().parse(t[1:])
                (lexeme, file, line, column) = t[0]
                self.expect("let statement", lexeme == ")", t[0], "expected ')'")
                tuples.append(nest)
            elif lexeme == "[":
                (nest, t) = LetLhsRule().parse(t[1:])
                (lexeme, file, line, column) = t[0]
                self.expect("let statement", lexeme == "]", t[0], "expected ']'")
                tuples.append(("nest", nest))
            else:
                return (("nest", tuples), t)
            (lexeme, file, line, column) = t[1]
            if lexeme != ",":
                if len(tuples) == 1:
                    return (tuples[0], t[1:])
                else:
                    return (("nest", tuples), t[1:])
            t = t[2:]

class StatementRule(Rule):
    def skip(self, token, t):
        (lex2, file2, line2, col2) = t[0]
        self.expect("statement", lex2 == ";", t[0], "expected a semicolon")
        (lex1, file1, line1, col1) = token
        if not ((line1 == line2) or (col1 == col2)):
            print("Parse warning: ';' does not line up", token, t[0])
        return t[1:]
        
    def parse(self, t):
        token = t[0]
        (lexeme, file, line, column) = token
        if lexeme == "const":
            const = t[1]
            (lexeme, file, line, column) = t[1]
            self.expect("constant definition", isname(lexeme), t[1], "expected name")
            (lexeme, file, line, column) = t[2]
            self.expect("constant definition", lexeme == "=", t[2], "expected '='")
            (ast, t) = NaryRule({";"}).parse(t[3:])
            return (ConstAST(const, ast), self.skip(token, t))
        if lexeme == "if":
            alts = []
            while True:
                (cond, t) = NaryRule({":"}).parse(t[1:])
                (stat, t) = StatListRule({ "else", "elif", ";" }).parse(t[1:])
                alts += [(cond, stat)]
                (lexeme, file, line, column) = t[0]
                if lexeme in { "else", ";" }:
                    break
                self.expect("if statement", lexeme == "elif", t[0],
                            "expected 'else' or 'elif' or semicolon")
            if lexeme == "else":
                (stat, t) = BlockRule({";"}).parse(t[1:])
            else:
                stat = None
            return (IfAST(alts, stat), self.skip(token, t))
        if lexeme == "while":
            (cond, t) = NaryRule({":"}).parse(t[1:])
            (stat, t) = StatListRule({";"}).parse(t[1:])
            return (WhileAST(cond, stat), self.skip(token, t))
        if lexeme == "for":
            var = t[1]
            (lexeme, file, line, column) = var
            self.expect("for statement", isname(lexeme), var, "expected name")
            (lexeme, file, line, column) = t[2]
            self.expect("for statement", lexeme == "in", t[2], "expected 'in'")
            (s, t) = NaryRule({":"}).parse(t[3:])
            (stat, t) = StatListRule({";"}).parse(t[1:])
            return (ForAST(var, s, stat), self.skip(token, t))
        if lexeme == "let":
            vars = []
            while True:
                (tuples, t) = LetLhsRule().parse(t[1:])
                (lexeme, file, line, column) = t[0]
                self.expect("let statement", lexeme == "=", t[0], "expected '='")
                (ast, t) = NaryRule({":", ","}).parse(t[1:])
                vars.append((tuples, ast))
                (lexeme, file, line, column) = t[0]
                if lexeme == ":":
                    break
                self.expect("let statement", lexeme == ",", t[0], "expected ',' or ':'")
            (stat, t) = StatListRule({";"}).parse(t[1:])
            return (LetAST(vars, stat), self.skip(token, t))
        if lexeme == "atomic":
            (stat, t) = BlockRule({";"}).parse(t[1:])
            return (AtomicAST(stat), self.skip(token, t))
        if lexeme == "del":
            (ast, t) = LValueRule().parse(t[1:])
            return (DelAST(ast), self.skip(token, t))
        if lexeme == "def" or lexeme == "fun":
            map = lexeme == "fun"
            name = t[1]
            (lexeme, file, line, column) = name
            self.expect("method definition", isname(lexeme), name, "expected name")
            (lexeme, file, line, column) = t[2]
            self.expect("method definition", lexeme == "(", t[2], "expected '('")
            arg = t[3]
            (lexeme, file, line, column) = arg
            if lexeme == ")":
                args = []
                (stat, t) = BlockRule({";"}).parse(t[4:])
            else:
                self.expect("method definition", isname(lexeme), arg,
                        "expected name or ')'")
                args = [arg]
                t = t[4:]
                (lexeme, file, line, column) = t[0]
                while lexeme != ")":
                    self.expect("method definition", lexeme == ",", t[0],
                                "expected ',' or ')'")
                    arg = t[1]
                    (lexeme, file, line, column) = arg
                    self.expect("method definition", isname(lexeme), arg,
                                "expected argument name")
                    args.append(arg)
                    t = t[2:]
                    (lexeme, file, line, column) = t[0]
                (stat, t) = BlockRule({";"}).parse(t[1:])
            return (MethodAST(name, args, stat, map), self.skip(token, t))
        if lexeme == "call":
            (expr, t) = ExpressionRule().parse(t[1:])
            (lexeme, file, line, column) = t[0]
            self.expect("call statement", lexeme == ";", t[0], "expected semicolon")
            return (CallAST(expr), self.skip(token, t))
        if lexeme == "spawn":
            (method, t) = BasicExpressionRule().parse(t[1:])
            (arg, t) = BasicExpressionRule().parse(t)
            (lexeme, file, line, column) = t[0]
            if lexeme == ",":
                (tag, t) = NaryRule({";"}).parse(t[1:])
                (lexeme, file, line, column) = t[0]
            else:
                tag = None
            self.expect("spawn statement", lexeme == ";", t[0], "expected semicolon")
            return (SpawnAST(tag, method, arg), self.skip(token, t))
        if lexeme == "trap":
            (method, t) = BasicExpressionRule().parse(t[1:])
            (arg, t) = BasicExpressionRule().parse(t)
            (lexeme, file, line, column) = t[0]
            self.expect("trap statement", lexeme == ";", t[0], "expected semicolon")
            return (TrapAST(method, arg), self.skip(token, t))
        if lexeme == "go":
            (ctx, t) = BasicExpressionRule().parse(t[1:])
            (result, t) = BasicExpressionRule().parse(t)
            (lexeme, file, line, column) = t[0]
            self.expect("go statement", lexeme == ";", t[0], "expected semicolon")
            return (GoAST(ctx, result), self.skip(token, t))
        if lexeme == "pass":
            return (PassAST(), self.skip(token, t[1:]))
        if lexeme == "import":
            (lexeme, file, line, column) = t[1]
            self.expect("import statement", isname(lexeme), t[1], "expected name")
            return (ImportAST(t[1]), self.skip(token, t[2:]))
        if lexeme == "assert":
            (cond, t) = NaryRule({",", ";"}).parse(t[1:])
            (lexeme, file, line, column) = t[0]
            if lexeme == ",":
                (expr, t) = NaryRule({";"}).parse(t[1:])
            else:
                self.expect("assert statement", lexeme == ";", t[0], "expected semicolon")
                expr = None
            return (AssertAST(token, cond, expr), self.skip(token, t))
        (ast, t) = LValueRule().parse(t)
        lvs = [ast]
        (lexeme, file, line, column) = op = t[0]
        while lexeme == ",":
            (ast, t) = LValueRule().parse(t[1:])
            lvs.append(ast)
            (lexeme, file, line, column) = op = t[0]
        if lexeme in [ "=", "+", "-", "*", "/", "and", "or" ]:
            if lexeme != "=":
                (eq, file, line, column) = t[1];
                self.expect("assignment statement", eq == "=", t[1],
                                                    "expected '='")
                t = t[1:]
            return AssignmentRule(lvs, op).parse(t[1:])
        self.expect("statement", lexeme == ";", t[0], "expected semicolon")

        # Turn LValue into an RValue
        assert isinstance(ast, LValueAST)
        assert lvs == [ast]         # TODO
        a = ast.indexes[0]
        args = ast.indexes[1:]
        while args != []:
            a = ApplyAST(a, args[0], ast.token)
            args = args[1:]
        return (CallAST(a), self.skip(token, t))

class ContextValue(Value):
    def __init__(self, nametag, pc):
        self.nametag = nametag
        self.pc = pc
        self.atomic = 0
        self.interruptable = False
        self.stack = []     # collections.deque() seems slightly slower
        self.fp = 0         # frame pointer
        self.vars = novalue
        self.trap = None
        self.terminated = False
        self.stopped = False
        self.failure = None

    def __repr__(self):
        return "ContextValue(" + str(self.nametag) + ", " + str(self.pc) + ")"

    def __str__(self):
        return self.__repr__()

    def __hash__(self):
        h = (self.nametag, self.pc, self.atomic, self.interruptable, self.vars,
            self.trap, self.terminated, self.stopped, self.failure).__hash__()
        for v in self.stack:
            h ^= v.__hash__()
        return h

    def __eq__(self, other):
        if not isinstance(other, ContextValue):
            return False
        if self.nametag != other.nametag:
            return False
        if self.pc != other.pc:
            return False
        if self.atomic != other.atomic:
            return False
        if self.interruptable != other.interruptable:
            return False
        if self.terminated != other.terminated:
            return False
        if self.stopped != other.stopped:
            return False
        if self.fp != other.fp:
            return False
        if self.trap != other.trap:
            return False
        if self.failure != other.failure:
            return False
        return self.stack == other.stack and self.vars == other.vars

    def copy(self):
        c = ContextValue(self.nametag, self.pc)
        c.atomic = self.atomic
        c.interruptable = self.interruptable
        c.stack = self.stack.copy()
        c.fp = self.fp
        c.trap = self.trap
        c.vars = self.vars
        c.terminated = self.terminated
        c.stopped = self.stopped
        c.failure = self.failure
        return c

    def get(self, var):
        return self.vars.d[var]

    def iget(self, indexes):
        v = self.vars
        while indexes != []:
            v = v.d[indexes[0]]
            indexes = indexes[1:]
        return v

    def update(self, record, indexes, val):
        if len(indexes) > 1:
            v = self.update(record.d[indexes[0]], indexes[1:], val)
        else:
            v = val
        d = record.d.copy()
        d[indexes[0]] = v
        return DictValue(d)

    def doDelete(self, record, indexes):
        if len(indexes) > 1:
            d = record.d.copy()
            d[indexes[0]] = self.doDelete(record.d[indexes[0]], indexes[1:])
        else:
            d = record.d.copy()
            if indexes[0] in d:
                del d[indexes[0]]
        return DictValue(d)

    def set(self, indexes, val):
        self.vars = self.update(self.vars, indexes, val)

    def delete(self, indexes):
        self.vars = self.doDelete(self.vars, indexes)

    def push(self, val):
        assert val != None
        self.stack.append(val)

    def pop(self):
        return self.stack.pop()

    def key(self):
        return (100, (key(self.nametag), self.pc, self.__hash__()))

class State:
    def __init__(self, code, labels):
        self.code = code
        self.labels = labels
        self.vars = novalue
        self.ctxbag = {}
        self.stopbag = {}
        self.choosing = None
        self.initializing = True

    def __repr__(self):
        return "State(" + str(self.vars) + ", " + str(self.ctxbag) + ", " + \
            str(self.stopbag) + ")"

    def __hash__(self):
        h = self.vars.__hash__()
        for c in self.ctxbag.items():
            h ^= c.__hash__()
        for c in self.stopbag.items():
            h ^= c.__hash__()
        return h

    def __eq__(self, other):
        if not isinstance(other, State):
            return False
        assert self.code == other.code and self.labels == other.labels
        if self.vars != other.vars:
            return False
        if self.ctxbag != other.ctxbag:
            return False
        if self.stopbag != other.stopbag:
            return False
        if self.choosing != other.choosing:
            return False
        if self.initializing != self.initializing:
            return False
        return True

    def copy(self):
        s = State(self.code, self.labels)
        s.vars = self.vars      # no need to copy as store operations do it
        s.ctxbag = self.ctxbag.copy()
        s.stopbag = self.stopbag.copy()
        s.choosing = self.choosing
        s.initializing = self.initializing
        return s

    def get(self, var):
        return self.vars.d[var]

    def iget(self, indexes):
        path = indexes
        v = self.vars
        while indexes != []:
            try:
                v = v.d[indexes[0]]
            except KeyError:
                print()
                print("no index", indexes[0], "in variable", path)
                exit(1)
            indexes = indexes[1:]
        return v

    def update(self, record, indexes, val):
        if len(indexes) > 1:
            v = self.update(record.d[indexes[0]], indexes[1:], val)
        else:
            v = val
        d = record.d.copy()
        d[indexes[0]] = v
        return DictValue(d)

    def doDelete(self, record, indexes):
        d = record.d.copy()
        if len(indexes) > 1:
            d[indexes[0]] = self.doDelete(record.d[indexes[0]], indexes[1:])
        else:
            del d[indexes[0]]
        return DictValue(d)

    def doStop(self, record, indexes, ctx):
        d = record.d.copy()
        if len(indexes) > 1:
            d[indexes[0]] = self.doStop(record.d[indexes[0]], indexes[1:], ctx)
        else:
            # TODO.  Should be print + set failure
            # TODO.  Make ctx a Harmony value
            list = d[indexes[0]]
            assert(isinstance(list, DictValue))
            d2 = list.d.copy()
            d2[len(d2)] = ctx
            d[indexes[0]] = DictValue(d2)
        return DictValue(d)

    def set(self, indexes, val):
        self.vars = self.update(self.vars, indexes, val)

    def delete(self, indexes):
        self.vars = self.doDelete(self.vars, indexes)

    def stop(self, indexes, ctx):
        self.vars = self.doStop(self.vars, indexes, ctx)
        cnt = self.stopbag.get(ctx)
        if cnt == None:
            self.stopbag[ctx] = 1
        else:
            self.stopbag[ctx] = cnt + 1

    def add(self, ctx):
        cnt = self.ctxbag.get(ctx)
        if cnt == None:
            self.ctxbag[ctx] = 1
        else:
            self.ctxbag[ctx] = cnt + 1

    def remove(self, ctx):
        cnt = self.ctxbag[ctx]
        assert cnt > 0
        if cnt == 1:
            del self.ctxbag[ctx]
        else:
            self.ctxbag[ctx] = cnt - 1

class Node:
    def __init__(self, parent, before, after, steps, len):
        global node_uid

        self.parent = parent    # next hop on way to initial state
        self.len = len          # length of path to initial state
        self.before = before    # the context that made the hop from the parent state
        self.after = after      # the resulting context
        self.steps = steps      # list of microsteps

        # if state.choosing, maps choice, else context
        self.edges = {}         # map to <nextState, nextContext, steps>

        self.sources = set()    # backward edges
        self.expanded = False   # lazy deletion
        self.issues = set()     # set of problems with this state
        self.uid = node_uid
        node_uid += 1

def strsteps(steps):
    if steps == None:
        return "[]"
    result = ""
    i = 0
    while i < len(steps):
        if result != "":
            result += ","
        (pc, choice) = steps[i]
        if pc == None:
            result += "Interrupt"
        else:
            result += str(pc)
        j = i + 1
        if choice != None:
            result += "(choose %s)"%strValue(choice)
        else:
            while j < len(steps):
                (pc2, choice2) = steps[j]
                if pc == None or pc2 != pc + 1 or choice2 != None:
                    break
                (pc, choice) = (pc2, choice2)
                j += 1
            if j > i + 1:
                result += "-%d"%pc
        i = j
    return "[" + result + "]"

def get_path(visited, state):
    if state == None:
        return []
    node = visited[state]
    return get_path(visited, node.parent) + [(node, state.vars)]

def nametag2str(nt):
    return str(nt.d["name"]) + "/" + str(nt.d["tag"])

def find_shortest(visited, bad):
    best_state = None
    best_len = 0
    for s in bad:
        node = visited[s]
        if best_state == None or node.len < best_len:
            best_state = s
            best_len = node.len
    return best_state

def print_path(visited, bad_state):
    path = genpath(bad_state, visited)
    for (ctx, steps, states, vars) in path:
        print(nametag2str(ctx.nametag), strsteps(steps))
    if len(path) > 0:
        (ctx, steps, states, vars) = path[-1]
        if ctx.failure != None:
            print(">>>", ctx.failure)

def print_shortest(visited, bad):
    bad_state = find_shortest(visited, bad)
    print_path(visited, bad_state)

class Scope:
    def __init__(self, parent):
        self.parent = parent
        self.names = {}
        self.locations = {}
        self.labels = {}

    def checkUnused(self, name):
        (lexeme, file, line, column) = name
        tv = self.names.get(lexeme)
        if tv != None:
            (t, v) = tv
            assert t != "variable", ("variable name in use", name, v)

    def lookup(self, name):
        (lexeme, file, line, column) = name
        tv = self.names.get(lexeme)
        if tv != None:
            return tv
        ancestor = self.parent
        while ancestor != None:
            tv = ancestor.names.get(lexeme)
            if tv != None:
                (t, v) = tv
                # if t == "variable":
                #    return None
                return tv
            ancestor = ancestor.parent
        return None

    def location(self, pc, file, line, labels):
        if self.parent == None:
            self.locations[pc] = (file, line)
            for (label, file, line, column) in labels:
                self.labels[label] = pc
        else:
            self.parent.location(pc, file, line, labels)

def optjump(code, pc):
    while pc < len(code):
        op = code[pc]
        if not isinstance(op, JumpOp):
            break
        pc = op.pc
    return pc

def optimize(code):
    for i in range(len(code)):
        op = code[i]
        if isinstance(op, JumpOp):
            code[i] = JumpOp(optjump(code, op.pc))
        elif isinstance(op, JumpCondOp):
            code[i] = JumpCondOp(op.cond, optjump(code, op.pc))

lasttime = 0

class Pad:
    def __init__(self, descr):
        self.descr = descr
        self.value = ""
        self.lastlen = 0
    
    def __repr__(self):
        return self.descr + " = " + self.value

    def pad(self, v):
        if len(v) < len(self.value):
            self.value = " " * (len(self.value) - len(v))
        else:
            self.value = ""
        self.value += v

p_ctx = Pad("ctx")
p_pc  = Pad("pc")
p_ns  = Pad("#states")
p_dia = Pad("diameter")
p_ql  = Pad("#queue")

# Have context ctx make one (macro) step in the given state
def onestep(state, ctx, choice, interrupt, visited, todo, node):
    assert ctx.failure == None, ctx.failure

    # Keep track of whether this is the same context as the parent context
    samectx = ctx == node.after

    # Copy the state before modifying it
    sc = state.copy()   # sc is "state copy"
    sc.choosing = None

    # Make a copy of the context before modifying it (cc is "context copy")
    cc = ctx.copy()

    # Copy the choice as well
    choice_copy = choice

    steps = []

    if interrupt:
        (method, arg) = ctx.trap
        cc.push(PcValue(cc.pc))
        cc.push("interrupt")
        cc.push(arg)
        cc.pc = method.pc
        cc.interruptable = False
        steps.append((None, None))      # indicates an interrupt

    localStates = set() # used to detect infinite loops
    loopcnt = 0         # only check for infinite loops after a while
    while not cc.terminated:
        # execute one microstep
        steps.append((cc.pc, choice_copy))

        # print status update
        global lasttime
        if time.time() - lasttime > 0.3:
            p_ctx.pad(nametag2str(cc.nametag))
            p_pc.pad(str(cc.pc))
            p_ns.pad(str(len(visited)))
            p_dia.pad(str(node.len))
            p_ql.pad(str(len(todo)))
            print(p_ctx, p_pc, p_ns, p_dia, p_ql, len(localStates), end="\r")
            lasttime = time.time()

        # If the current instruction is a "choose" instruction,
        # make the specified choice
        if isinstance(sc.code[cc.pc], ChooseOp):
            assert choice_copy != None;
            cc.stack[-1] = choice_copy
            cc.pc += 1
            choice_copy = None
        else:
            assert choice_copy == None
            try:
                sc.code[cc.pc].eval(sc, cc)
            except Exception as e:
                traceback.print_exc()
                exit(1)
                cc.failure = "Python assertion failed"

        if cc.failure != None or cc.stopped:
            break

        # See if this process is making a nondeterministic choice.
        # If so, we break out of the microstep loop.  However, only
        # this process is scheduled from this state.
        if isinstance(sc.code[cc.pc], ChooseOp):
            v = cc.stack[-1]
            if (not isinstance(v, SetValue)) or v.s == set():
                # TODO.  Need the location of the choose operation in the file
                cc.failure = "pc = " + str(cc.pc) + \
                    ": Error: choose can only be applied to non-empty sets"
                break

            # if there is no other process, we can just keep going
            if len(v.s) > 1:
                sc.choosing = cc
                break
            else:
                choice_copy = list(v.s)[0]

        # if we're about to do a state change, let other processes
        # go first assuming there are other processes and we're not
        # in "atomic" mode
        if cc.atomic == 0 and type(sc.code[cc.pc]) in { LoadOp, StoreOp }: # TODO  and len(sc.ctxbag) > 1:
            break
        if cc.atomic == 0 and type(sc.code[cc.pc]) in { AtomicIncOp }:
            break

        # ContinueOp always causes a break
        if isinstance(sc.code[cc.pc], ContinueOp):
            break

        # Detect infinite loops if there's a suspicion
        loopcnt += 1
        if loopcnt > 200:
            if (sc, cc) in localStates:
                cc.failure = "infinite loop"
                break
            localStates.add((sc.copy(), cc.copy()))

    # Remove original context from bag
    sc.remove(ctx)

    # Put the resulting context into the bag unless it's done
    if cc.terminated:
        sc.initializing = False     # initializing ends when __init__ finishes
    elif not cc.stopped:
        sc.add(cc)

    length = node.len if samectx else (node.len + 1)
    next = visited.get(sc)
    if next == None:
        next = Node(state, ctx, cc, steps, length)
        visited[sc] = next
        if samectx:
            todo.insert(0, sc)
        else:
            todo.append(sc)
    elif next.len > length:
        assert length == node.len and next.len == node.len + 1 and not next.expanded, (node.len, length, next.len, next.expanded)
        # assert not next.expanded, (node.len, length, next.len, next.expanded)
        next.len = length
        next.parent = state
        next.before = ctx
        next.after = cc
        next.steps = steps
        todo.insert(0, sc)
    node.edges[choice if state.choosing else ctx] = (sc, cc, steps)
    next.sources.add(state)
    if cc.failure != None:
        next.issues.add("process failure")

def parseConstant(c, v):
    tokens = lexer(v, "<constant argument>")
    try:
        (ast, rem) = ExpressionRule().parse(tokens)
    except IndexError:
        # best guess...
        print("Parsing constant", v, "hit end of string")
        exit(1)
    scope = Scope(None)
    code = []
    ast.compile(scope, code)
    state = State(code, scope.labels)
    ctx = ContextValue(DictValue({"name": "__arg__", "tag": novalue}), 0)
    ctx.atomic = 1
    while ctx.pc != len(code):
        code[ctx.pc].eval(state, ctx)
    constants[c] = ctx.pop()

def doCompile(filenames, consts, mods):
    for c in consts:
        try:
            i = c.index("=")
            parseConstant(c[0:i], c[i+1:])
        except IndexError:
            print("Usage: -c C=V to define a constant")
            exit(1)

    global modules
    for m in mods:
        try:
            i = m.index("=")
            modules[m[0:i]] = m[i+1:]
        except IndexError:
            print("Usage: -m module=version to specify a module version")
            exit(1)

    scope = Scope(None)
    code = []
    if filenames == []:
        if False:
            print("Loading code from standard input...")
            load(sys.stdin, "<stdin>", scope, code)
        else:
            usage()
    else:
        for fname in filenames:
            if os.path.exists(fname):
                with open(fname) as fd:
                    load(fd, fname, scope, code)
            else:
                print("Can't open", fname, file=sys.stderr)
                exit(1)
    code.append(ReturnOp())     # to terminate "__init__" process
    optimize(code)
    return (code, scope)

# No longer using this code
def explore(s, visited, mapping, reach):
    reach[s] = None         # prevent infinite loops
    hs = mapping[s]
    n = visited[s]
    result = set()
    assert not s.choosing
    for (ctx, edge) in n.edges.items():
        assert isinstance(ctx, ContextValue)
        (nextState, nextContext, nextSteps) = edge
        next = mapping[nextState]
        if next == hs:
            if nextState not in reach:
                explore(nextState, visited, mapping, reach)
            r = reach[nextState]
            if r != None:
                result = result.union(r)
        else:
            result.add(next)
    reach[s] = result

# No longer using this code
def mapcheck():
    # TODO.  Maybe should be done immediately after computing new state
    if map != None:
        # Compute low -> high mapping
        mapping = {}
        attainable = set()
        desirable = set()
        for s in visited.keys():
            if s.initializing:
                continue
            sc = s.copy()

            # Map low-level state to high-level state
            assert isinstance(map, PcValue)
            frame = code[map.pc]
            assert isinstance(frame, FrameOp)
            ctx = ContextValue("__map__", map.pc)
            ctx.atomic = 1          # TODO.  Maybe map should be atomic
            ctx.push(novalue)
            while ctx.pc != frame.end:
                code[ctx.pc].eval(sc, ctx)
                assert sc.vars == s.vars    # TODO.  map should be read-only
            hs = ctx.vars.d["result"]
            mapping[s] = hs
            attainable.add(hs)

        # mapping[s] is high-level state as function of low-level state s
        # attainable is the set of high-level states corresponding to the set of low-level states

        # See what high-level states can be reached from each high-level state
        hstep = {}
        for hs in attainable:
            # Map high-level step to next high-level states
            # TODO.  Share code with __map__ (and other such places)
            assert isinstance(step, PcValue)
            frame = code[step.pc]
            assert isinstance(frame, FrameOp)
            ctx = ContextValue("__step__", step.pc)
            ctx.atomic = 1          # TODO.  Maybe map should be atomic
            ctx.push(hs)
            while ctx.pc != frame.end:
                code[ctx.pc].eval(sc, ctx)
                assert sc.vars == s.vars    # TODO.  Maybe map should be read-only
            next = ctx.vars.d["result"]
            assert isinstance(next, SetValue), next
            desirable = desirable.union(next.s)
            hstep[hs] = next.s

        # hstep[hs] is the set of high-level states that are reachable from hs
        # desirable is the set of high-level states reachable from attainable

        # See which high level states can be reached from each low level state
        reach = {}
        for s in visited.keys():
            explore(s, visited, mapping, reach)

        # reach[s] is the set of high level states reachable from low level state s

        # Make sure each low-level step is allowed
        for (s, next) in reach.items():
            for hs in next:
                assert hs in hstep[mapping[s]], (s, mapping[s], hstep[mapping[s]], hs)

        # Now see if every desirable high level state can be reached
        for s in visited.keys():
            hs = mapping[s]
            if hs in desirable:
                desirable.remove(hs)
        assert desirable == set(), desirable

def run(code, labels, map, step, blockflag):
    state = State(code, labels)
    ctx = ContextValue(DictValue({"name": "__init__", "tag": novalue}), 0)
    ctx.atomic = 1
    state.add(ctx)

    # For traversing Kripke graph
    visited = { state: Node(None, None, None, [], 0) }
    todo = collections.deque([state])
    bad = set()

    faultyState = False
    maxdiameter = 0
    while todo:
        state = todo.popleft()
        node = visited[state]
        if len(node.issues) > 0:
            bad.add(state)
            faultyState = True
            break
        if node.expanded:
            continue
        node.expanded = True
        lastState = state
        if node.len > maxdiameter:
            maxdiameter = node.len

        if state.choosing != None:
            ctx = state.choosing
            assert ctx in state.ctxbag, ctx
            choices = ctx.stack[-1]
            assert isinstance(choices, SetValue), choices
            assert len(choices.s) > 0
            for choice in choices.s:
                onestep(state, ctx, choice, False, visited, todo, node)
        else:
            for (ctx, _) in state.ctxbag.items():
                onestep(state, ctx, None, False, visited, todo, node)
                if ctx.interruptable:
                    onestep(state, ctx, None, True, visited, todo, node)

    print("#states =", len(visited), "diameter =", maxdiameter, " "*100 + "\b"*100)

    todump = set()

    # See if there has been a safety violation
    issues_found = False
    if len(bad) > 0:
        print("==== Safety violation ====")
        bad_state = find_shortest(visited, bad)
        print_path(visited, bad_state)
        todump.add(bad_state)
        issues_found = True

    if not faultyState:
        # See if all processes "can" terminate.  First look for
        # states where there are no processes.
        term = set()
        for (s, n) in visited.items():
            if blockflag:
                # see if all processes are blocked
                if len(s.ctxbag) > 0:
                    someRunning = False
                    assert not s.choosing
                    for (ctx, next) in n.edges.items():
                        assert isinstance(ctx, ContextValue)
                        (nxtstate, nxtctx, steps) = next
                        if nxtstate != s:
                            someRunning = True
                            break
                    if not someRunning:
                        term.add(s)
            elif len(s.ctxbag) == 0:
                term.add(s)

        # Now find all the states that can reach terminating states.
        nextgood = term
        while nextgood != set():
            newgood = set()
            for s in nextgood:
                for s2 in visited[s].sources.difference(term):
                    newgood.add(s2)
            term = term.union(newgood)
            nextgood = newgood
        bad = set(visited.keys()).difference(term)
        if len(bad) > 0:
            print("==== Non-terminating States ====", len(bad))
            for s in bad:
                visited[s].issues.add("non-terminating state")
            bad_state = find_shortest(visited, bad)
            print_path(visited, bad_state)
            todump.add(bad_state)
            issues_found = True

            # See which processes are blocked
            node = visited[bad_state]
            assert not bad_state.choosing
            running = 0
            blocked = 0
            for (ctx, next) in node.edges.items():
                assert isinstance(ctx, ContextValue)
                (nxtstate, nxtctx, steps) = next
                if nxtstate == bad_state:
                    blocked += 1
                    print("blocked process:", ctx)
                else:
                    running += 1
                    print("running process:", ctx)
            print("#blocked:", blocked, "#running:", running)

    # Look for states where there are no processes running but there are blocked processes.
    bad = set()
    for (s, n) in visited.items():
        if len(s.ctxbag) == 0 and len(s.stopbag) > 0:
            bad.add(s)
            visited[s].issues.add("stopped process")
    if len(bad) > 0:
        print("==== Stopped States ====", len(bad))
        bad_state = find_shortest(visited, bad)
        todump.add(bad_state)
        print_path(visited, bad_state)
        for ctx in bad_state.stopbag.keys():
            print("stopped process:", ctx)
        issues_found = True

    if not issues_found:
        print("no issues found")
        s = lastState
    else:
        s = find_shortest(visited, todump)
    return (visited, s)

def htmlstrsteps(steps):
    if steps == None:
        return "[]"
    result = ""
    i = 0
    while i < len(steps):
        if result != "":
            result += " "
        (pc, choice) = steps[i]
        j = i + 1
        if pc == None:
            result += "Interrupt"
        else:
            result += "<a href='#P%d'>%d"%(pc, pc)
        if choice != None:
            result += "</a>(choose %s)"%strValue(choice)
        else:
            while j < len(steps):
                (pc2, choice2) = steps[j]
                if pc == None or pc2 != pc + 1 or choice2 != None:
                    break
                (pc, choice) = (pc2, choice2)
                j += 1
            if j > i + 1:
                result += "-%d"%pc
            result += "</a>"
        i = j
    return result

def genpath(s, visited):
    # Extract the path to s
    path = []
    while s != None:
        n = visited[s]
        if n.after == None:
            break
        path = [(s, n)] + path
        s = n.parent

    # Now compress the path, combining macrosteps by the same context
    path2 = []
    lastctx = None
    laststeps = []
    laststates = []
    lastvars = DictValue({})
    for (s, n) in path:
        if lastctx == None or lastctx == n.before:
            laststeps += n.steps
            lastctx = n.after
            laststates.append(n.uid)
            lastvars = s.vars
            continue
        path2.append((lastctx, laststeps, laststates, lastvars))
        lastctx = n.after
        laststeps = n.steps.copy()
        laststates = []
    path2.append((lastctx, laststeps, laststates, lastvars))
    return path2

def htmlpath(s, visited, color, f):
    # Generate a label for the path table
    issues = visited[s].issues
    if len(issues) == 0:
        issues = { "no issues" }
    label = ""
    for issue in issues:
        if label != "":
            label += ", "
        label += issue
    label = "Issue: " + label
    keys = sorted(s.vars.d.keys(), key=keyValue)
    path = genpath(s, visited)
    print("<table id='issuestbl' border='1' width='100%%'><tr><th colspan='2' align='left' style='color: %s'>%s</th><th></th>"%(color, html.escape(label)), file=f)
    if len(keys) == 1:
        print("<th>Shared Variable</th>", file=f)
    else:
        print("<th colspan='%d'>Shared Variables</th>"%len(keys), file=f)
    print("<col style='width:15%'>", file=f)
    print("<tr><th>Process</th><th>Steps</th><th></th>", file=f)
    for v in keys:
        print("<td align='center' style='font-style: italic'>%s</td>"%v, file=f)
    print("</tr><tr><td><td></tr>", file=f)
    row = 1
    for (ctx, steps, states, vars) in path:
        row += 1
        if len(states) > 0:
            sid = states[-1]
        else:
            sid = visited[s].uid
        print("<tr><td><a href='javascript:rowshow(%d,%d)'>%s</a></td>"%(row, sid, nametag2str(ctx.nametag)), file=f)
        print("<td>%s</td><td></td>"%htmlstrsteps(steps), file=f)

        for k in keys:
            print("<td align='center'>%s</td>"%strValue(vars.d[k]), file=f)
        print("</tr>", file=f)
    print("</table>", file=f)

def htmlloc(code, scope, ctx, traceid, f):
    pc = ctx.pc
    fp = ctx.fp
    print("<table id='loc%d' border='1' width='100%%'>"%traceid, file=f)
    trace = []
    while True:
        trace += [(pc, fp)]
        if fp < 5:
            break
        pc = ctx.stack[fp - 5]
        assert isinstance(pc, PcValue)
        pc = pc.pc
        fp = ctx.stack[fp - 1]
    trace.reverse()
    row = 0
    for (pc, fp) in trace:
        if row == 0:
            print("<tr style='background-color: #A5FF33'>", file=f)
        else:
            print("<tr>", file=f)
        print("<td>", file=f)
        print("<a href='#P%d'>%d</a> "%(pc, pc), file=f)
        print("<a href='javascript:setrow(%d,%d)'>"%(traceid,row), file=f)
        while pc >= 0 and pc not in scope.locations:
            pc -= 1
        (file, line) = scope.locations[pc]
        while pc >= 0 and not isinstance(code[pc], FrameOp):
            pc -= 1
        if fp >= 3:
            arg = ctx.stack[fp-3]
            if arg == novalue:
                print("%s()"%(code[pc].name[0]), end="", file=f)
            else:
                print("%s(%s)"%(code[pc].name[0], strValue(arg)), end="", file=f)
        print("</a>:", file=f)
        lines = files.get(file)
        if lines != None and line <= len(lines):
            print(html.escape(lines[line - 1]), file=f)
        print("</td></tr>", file=f)
        row += 1

    if ctx.failure != None:
        print("<tr style='color: red'><td>%s</td></tr>"%ctx.failure, file=f)
    print("</table>", file=f)

def htmlvars(vars, id, row, f):
    assert(isinstance(vars, DictValue))
    display = "block" if row == 0 else "none"
    print("<div id='vars%d_%d' style='display:%s'>"%(traceid, row, display), file=f)
    if len(vars.d) > 0:
        print("<table>", file=f)
        for (key, value) in vars.d.items():
            print("<tr>", file=f)
            print("<td>%s = %s</td>"%(strValue(key)[1:], strValue(value)), file=f)
            print("</tr>", file=f)
        print("</table>", file=f)
    print("</div>", file=f)

# print the variables on the stack
def htmltrace(code, scope, ctx, traceid, f):
    pc = ctx.pc
    fp = ctx.fp
    trace = [ctx.vars]
    while True:
        if fp < 4:
            break
        trace += [ctx.stack[fp - 2]]
        fp = ctx.stack[fp - 1]
    trace.reverse()
    for i in range(len(trace)):
        htmlvars(trace[i], traceid, i, f)

traceid = 0

def htmlrow(ctx, bag, state, node, code, scope, f, verbose):
    global traceid
    traceid += 1

    print("<tr>", file=f)
    if bag[ctx] > 1:
        print("<td>%s [%d copies]</td>"%(nametag2str(ctx.nametag), bag[ctx]), file=f)
    else:
        print("<td>%s</td>"%nametag2str(ctx.nametag), file=f)
    if ctx.stopped:
        print("<td>stopped</td>", file=f)
    else:
        if state.choosing:
            print("<td>choosing</td>", file=f)
        else:
            if ctx in node.edges:
                (nxtstate, nxtctx, steps) = node.edges[ctx]
                if nxtstate == state:
                    print("<td>blocked</td>", file=f)
                else:
                    print("<td>running</td>", file=f)
            else:
                print("<td>failed</td>", file=f)

    print("<td>", file=f)
    htmlloc(code, scope, ctx, traceid, f)
    print("</td>", file=f)

    # print variables
    print("<td>", file=f)
    htmltrace(code, scope, ctx, traceid, f)
    print("</td>", file=f)

    # print stack
    if verbose:
        print("<td>%d</td>"%ctx.fp, file=f)
        print("<td align='center'>", file=f)
        print("<table border='1'>", file=f)
        for v in ctx.stack:
            print("<tr><td align='center'>", file=f)
            if isinstance(v, PcValue):
                print("<a href='#P%d'>"%v.pc, file=f)
                print("%s"%strValue(v), file=f)
                print("</a>", file=f)
            else:
                print("%s"%strValue(v), file=f)
            print("</td></tr>", file=f)
        print("</table>", file=f)
        print("</td>", file=f)
        assert not s.choosing
        if ctx in n.edges:
            (ns, nc, steps) = n.edges[ctx]
            print("<td>%s</td>"%htmlstrsteps(steps), file=f)
            nn = visited[ns]
            print("<td><a href='javascript:show(%d)'>"%nn.uid, file=f)
            print("%d</a></td>"%nn.uid, file=f)
        else:
            print("<td>no steps</td>", file=f)
            print("<td></td>", file=f)
    print("</tr>", file=f)

def htmlstate(f):
    print("<table border='1' width='90%'>", file=f)
    print("<col style='width:20%'>", file=f)
    print("<col style='width:80%'>", file=f)

    print("<tr><td>state id</td><td>%d</td></tr>"%n.uid, file=f)
    # if s.failure != None:
    #     print("<tr><td>status</td><td>failure</td></tr>", file=f)
    if s.initializing:
        print("<tr><td>status</td><td>initializing</td></tr>", file=f)
    elif len(s.ctxbag) == 0:
        if len(s.stopbag) == 0:
            print("<tr><td>status</td><td>terminal</td></tr>", file=f)
        else:
            print("<tr><td>status</td><td>stopped</td></tr>", file=f)
    else:
        print("<tr><td>status</td><td>normal</td></tr>", file=f)

    if verbose:
        print("<tr><td>from</td>", file=f)
        print("<td><table><tr>", file=f)
        for src in sorted(n.sources, key=lambda x: (visited[x].len, visited[x].uid)):
            sid = visited[src].uid
            print("<td><a href='javascript:show(%d)'>%d</td>"%(sid, sid), file=f)
        print("</tr></table></td></tr>", file=f)

    if s.choosing != None:
        print("<tr><td>choosing</td><td>%s</td></tr>"%nametag2str(s.choosing.nametag), file=f)

    print("</table>", file=f)

def htmlnode(s, visited, code, scope, f, verbose):
    n = visited[s]
    print("<div id='div%d' style='display:none'>"%n.uid, file=f);
    print("<div class='container'>", file=f)

    print("<a name='N%d'/>"%n.uid, file=f)

    if False:
        print("<table border='1' width='100%'>", file=f)
        print("<tr><th>Shared Variables</th></tr>", file=f)
        for (key, value) in s.vars.d.items():
            print("<tr>", file=f)
            print("<td>%s = %s</td>"%(strValue(key)[1:], strValue(value)), file=f)
            print("</tr>", file=f)
        print("</table>", file=f)

    if verbose:
        print("<td>", file=f)
        htmlpath(s, visited, "black", f)
        print("</td>", file=f)

    # if s.failure != None:
    #     print("<table border='1' style='color: red'><tr><td>Failure:</td>", file=f)
    #     print("<td>%s</td>"%s.failure, file=f)
    #     print("</tr></table>", file=f)

    print("<table border='1'>", file=f)
    print("<tr><th>Process</th><th>Status</th><th>Stack Trace</th><th>Variables</th>", file=f)
    if verbose:
        print("<th>FP</th><th>Stack</th>", file=f)
        print("<th>Steps</th><th>Next State</th></tr>", file=f)
    else:
        print("</tr>", file=f)
        print("<tr><td></td><td></td><td></td><td></td></tr>", file=f)
    for ctx in sorted(s.ctxbag.keys(), key=lambda x: nametag2str(x.nametag)):
        htmlrow(ctx, s.ctxbag, s, n, code, scope, f, verbose)
    for ctx in sorted(s.stopbag.keys(), key=lambda x: nametag2str(x.nametag)):
        htmlrow(ctx, s.stopbag, s, n, code, scope, f, verbose)

    print("</table>", file=f)
    print("</div>", file=f);
    print("</div>", file=f);

def htmlcode(code, scope, f):
    print("<div id='table-wrapper'>", file=f)
    print("<div id='table-scroll'>", file=f)
    print("<table border='1'>", file=f)
    print("<tbody>", file=f)
    lastloc = None
    for pc in range(len(code)):
        print("<tr>", file=f)
        if scope.locations.get(pc) != None:
            (file, line) = scope.locations[pc]
            if (file, line) != lastloc:
                lines = files.get(file)
                if lines != None and line <= len(lines):
                    print("<th colspan='3' align='left' style='background-color: yellow'>%s:%d"%(html.escape(os.path.basename(file)), line),
                        html.escape(lines[line - 1]), "</th>", file=f)
                else:
                    print("<th colspan='2' align='left'>Line", line, "</th>", file=f)
                print("</tr><tr>", file=f)
            lastloc = (file, line)
        print("<td><a name='P%d'>"%pc, pc, "</a></td><td>", file=f)
        print("<span title='%s'>"%html.escape(code[pc].explain()), file=f)
        if isinstance(code[pc], JumpOp) or isinstance(code[pc], JumpCondOp):
            print("<a href='#P%d'>"%code[pc].pc, code[pc], "</a>", file=f)
        elif isinstance(code[pc], PushOp) and isinstance(code[pc].constant[0], PcValue):
            print("Push <a href='#P%d'>"%code[pc].constant[0].pc, strValue(code[pc].constant[0]), "</a>", file=f)
        else:
            print(html.escape(str(code[pc])), file=f)
        print("</span></td></tr>", file=f)
    print("</tbody>", file=f)
    print("</table>", file=f)
    print("</div>", file=f)
    print("</div>", file=f)

def htmldump(visited, code, scope, state, fulldump, verbose):
    with open("harmony.html", "w") as f:
        print("""
<html>
  <head>
    <style>
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
    </style>
  </head>
  <body>
        """, file=f)

        print("<table>", file=f)
        print("<col style='width:50%'>", file=f)
        print("<col style='width:50%'>", file=f)

        if state != None:
            print("<tr><td colspan='2'>", file=f)
            htmlpath(state, visited, "red", f)
            print("</td></tr>", file=f)
            print("<tr><td></td></tr>", file=f)

        print("<tr>", file=f)

        print("<td valign='top'>", file=f)
        htmlcode(code, scope, f)
        print("</td>", file=f)

        print("<td valign='top'>", file=f)
        if fulldump:
            for s in visited.keys():
                htmlnode(s, visited, code, scope, f, verbose)
        else:
            if state == None:
                cnt = 0
                for s in visited.keys():
                    htmlnode(s, visited, code, scope, f, verbose)
                    cnt += 1
                    if not fulldump and cnt > 100:
                        break
            else:
                s = state
                while s != None:
                    htmlnode(s, visited, code, scope, f, verbose)
                    s = visited[s].parent
        print("</td>", file=f)
        print("</tr>", file=f)
        print("</table>", file=f)

        if state == None:
            row = 0
            sid = 1
        else:
            row = visited[state].len + 1
            sid = visited[state].uid
        print(
            """
                <div id='divNone' style='display:none';>
                  <div class='container'>
                    <p>
                        State information not available.
                        Use harmony -d for a complete htmldump.
                    </p>
                  </div>
                </div>

                <script>
                  var current = 1;

                  function show(id) {
                      x = document.getElementById('div' + current);
                      if (x == null) {
                          x = document.getElementById('divNone')
                      }
                      x.style.display = 'none';
                      x = document.getElementById('div' + id)
                      if (x == null) {
                          x = document.getElementById('divNone')
                      }
                      x.style.display = 'block';
                      current = id;
                  }

                  function rowshow(row, id) {
                    show(id);
                    var tbl = document.getElementById("issuestbl");
                    for (var i = 1; i < tbl.rows.length; i++) {
                        if (i == row + 1) {
                            tbl.rows[i].style.backgroundColor = "#A5FF33";
                        }
                        else {
                            tbl.rows[i].style.backgroundColor = "";
                        }
                    }
                  }

                  function setrow(tblid, row) {
                    var tbl = document.getElementById('loc' + tblid);
                    for (var i = 0; i < tbl.rows.length; i++) {
                        var div = document.getElementById('vars' + tblid + '_' + i);
                        if (i == row) {
                            tbl.rows[i].style.backgroundColor = "#A5FF33";
                            div.style.display = 'block';
                        }
                        else {
                            tbl.rows[i].style.backgroundColor = "";
                            div.style.display = 'none';
                        }
                    }
                  }

                  rowshow(%d, %d)
                </script>
            """%(row, sid), file=f)
        print("</body>", file=f)
        print("</html>", file=f)
    print("Open file://" + os.getcwd() + "/harmony.html for more information")

def usage():
    print("Usage: harmony [options] harmony-file ...")
    print("  options: ")
    print("    -a: list machine code")
    print("    -b: blocking execution")
    print("    -c name=value: define a constant")
    print("    -d: htmldump full state into html file")
    print("    -h: help")
    print("    -m module=version: select a module version")
    exit(1)

def main():
    # Get options.  First set default values
    consts = []
    mods = []
    printCode = False
    blockflag = False
    fulldump = False
    try:
        opts, args = getopt.getopt(sys.argv[1:],
                        "abc:dhm:", ["const=", "help", "module="])
    except getopt.GetoptError as err:
        print(str(err))
        usage()
    for o, a in opts:
        if o == "-a":
            printCode = True
        elif o == "-b":
            blockflag = True
        elif o in { "-c", "--const" }:
            consts.append(a)
        elif o == "-d":
            fulldump = True
        elif o in { "-m", "--module" }:
            mods.append(a)
        elif o in { "-h", "--help" }:
            usage()
        else:
            assert False, "unhandled option"

    (code, scope) = doCompile(args, consts, mods)

    if printCode:
        lastloc = None
        for pc in range(len(code)):
            if scope.locations.get(pc) != None:
                (file, line) = scope.locations[pc]
                if (file, line) != lastloc:
                    lines = files.get(file)
                    if lines != None and line <= len(lines):
                        print("%s:%d"%(file, line), lines[line - 1])
                    else:
                        print(file, ":", line)
                lastloc = (file, line)
            print("  ", pc, code[pc])

    m = scope.names.get("__mutex__")
    if m == None:
        mpc = None
    else:
        (t, v) = m
        assert t == "constant"
        (mpc, file, line, column) = v
    s = scope.names.get("__step__")
    if s == None:
        spc = None
    else:
        (t, v) = s
        assert t == "constant"
        (spc, file, line, column) = v

    if not printCode:
        (visited, bad_state) = run(code, scope.labels, mpc, spc, blockflag)
        htmldump(visited, code, scope, bad_state, fulldump, False)

if __name__ == "__main__":
    main()