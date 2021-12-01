#ifndef SRC_CHARM_H
#define SRC_CHARM_H

#ifndef HARMONY_COMBINE
#include "minheap.h"
#include "code.h"
#include "value.h"
#include "graph.h"
#endif

struct global_t {
    struct code_t code;
    struct values_t values;
    struct graph_t graph;
    struct minheap *failures;    // queue of "struct failure"  (TODO: make part of struct node "issues")
    uint64_t *processes;         // list of contexts of processes
    int nprocesses;              // the number of processes in the list
    double lasttime;             // since last report printed
    int enqueued;                // #states enqueued
    int dequeued;                // #states dequeued
    bool dumpfirst;              // for json dumping
    struct dict *possibly_cnt;
};

#endif //SRC_CHARM_H