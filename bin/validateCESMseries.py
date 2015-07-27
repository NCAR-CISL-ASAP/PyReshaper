#!/usr/bin/env python

"""
This script aids in the validation of data for the timeseries files
created using "slice2series". It uses multiple parallel processes to
perform the validation using the multiprocessing module. This module 
is used instead of mpi4py because it provides convenient communincation
vectors (such as Queues) between processes that are suitable to the
task at hand. 
"""

import numpy as np
import multiprocessing
from multiprocessing import Process, Queue
import logging
import argparse
import Nio
import glob


def get_unlimited_dimension(series_file):
    """
    DESCRIPTION
        Get the name of the unlimited dimension in the timeseries files.
        Asusmes that:
            (i) there is only one unlimited dimension
            (ii) that the unlimited dimension is the same in all timeseries files
    ARGUMENTS
        series_file - One timeseries file
    RETURNS 
        Name of the unlimited dimension
    """
    ncid = Nio.open_file(series_file, "r")

    unlimitd_dim = None
    for dim in ncid.dimensions:
        if ncid.unlimited(dim): 
            unlimited_dim = dim
            break
    ncid.close()
    if (unlimited_dim == None):
        raise ValueError("No unlimited dimension found in timeseries file {0}".format(series_file))
    return unlimited_dim



def worker(work_queue, return_queue, unlim_dim, slice_files):
    """
    DESCRIPTION
        The main code that will be executed by all workers processes. 
    ARGUMENTS
        work_queue  - A multiprocessing.Queue object from which the function will
                      extract a timeseries filename to test. 
        return_queue- A queue in which to return if the validation succeeded
        unlim_dim   - Name of the unlimited dimension in the timeseries files
        slice_files - List of time-slice files that were used in the construction of
                      the timeseries files. 
    RETURNS

    """
    logger = multiprocessing.get_logger()  # get the logger for the multiprocessing module

    opened_files = []
    for fname in slice_files:
        try:
            opened_files.append(Nio.open_file(fname, "r"))
        except:
            raise IOError("Error while opening {0}".format(fname))


    while True:
        # Infinite loop. The worker stops when the poison pill is encountered
        series_file = work_queue.get()  # Get a piece of item
        
        # Poison pill
        if series_file == "STOP": break
        
        logger.info(series_file)

        all_okay   = True
        num_errors = 0
        result     = {}
        error_vars = []

        ncfile = Nio.open_file(series_file, "r")

        time_indep_vars = []   # Time dependent variables
        time_dep_vars   = []   # Time independent variables
        variables = ncfile.variables
        for var_name in variables.keys():
            var = variables[var_name]
            if unlim_dim not in var.dimensions:
                time_indep_vars.append(var_name)
            else:
                time_dep_vars.append(var_name)

        # Loop through all the time dependent variables
        for var_name in time_dep_vars:
            var = variables[var_name]

            # Checking 'non-string' variables only
            if (np.dtype(var.typecode()) != np.dtype('S1')):

                if len(var.dimensions) != 1: # not scalar
                    for i, _slice in enumerate(opened_files):
                        try:
                            assert(np.allclose(var[i,:], _slice.variables[var_name][0,:]))
                        except AssertionError:
                            all_okay = False
                            num_errors += 1
                            error_vars.append(var_name)
                else: # scalar
                    for i, _slice in enumerate(opened_files):
                        try:
                            assert(np.allclose(var[i], _slice.variables[var_name][:]))
                        except AssertionError:
                            all_okay = False
                            num_errors += 1
                            error_vars.append(var_name)


        ncfile.close()
        
        result["pass"] = all_okay
        result["num_errors"] = num_errors
        result["error_vars"] = error_vars
        result["filename"]   = series_file
        return_queue.put(result)
    return



if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="validateCESMseries.py")
    parser.add_argument('prefix',      type=str, help='filename prefix')
    parser.add_argument('slice_files', nargs='+', type=str, help="All time slice files used "
                                       "in the creation of the timeseries files")
    parser.add_argument('-n',    type=int, default=4, help='number of parallel processes')


    args = parser.parse_args()

    slice_files = args.slice_files
    num_slice_files = len(slice_files)

    print("Configurations for validator script:")
    print("Filename prefix : {0}".format(args.prefix))
    print("Number of time-series files found : {0}".format(num_slice_files))


    print("Generating collection of timeseries files ... ")
    series_files = glob.glob(args.prefix + "*")

    # Query the unlimited dimension name on one of the files
    unlim_dim = get_unlimited_dimension(series_files[0])

    # These are the Queues we'll use to send and receive data from the worker
    # processes
    work_queue   = Queue()
    return_queue = Queue()

    # Logger that takes care of pretty printing to the screen from parallel processes
    logger = multiprocessing.log_to_stderr()
    logger.setLevel(logging.INFO)

    # Putting 'jobs', i.e. timeseries files into the work queue
    for fname in series_files: work_queue.put(fname)

    processes = []  # List of parallel processes
    nprocs    = args.n
    for i in range(nprocs):
        p = Process(target=worker, args=(work_queue, return_queue,
                                         unlim_dim, slice_files))
        p.start()
        processes.append(p)
        work_queue.put('STOP')  # Putting a poison pill for the workers

    # Wait for jobs to complete
    for p in processes: p.join()

    # Checking results >>>>>>>>>
    files_pass = 0  # Counter for number of timeseries files that passed validation
    files_fail = 0  # Counter for number of timeseries files that failed validation
    
    for i in xrange(len(series_files)):
        result = return_queue.get()
        if result['pass'] == False:
            print("FAIL: Variables {0} in file {1}".format(result['error_vars'], result['filename']))
            files_fail += 1
        else:
            files_pass += 1

    print("-"*43)
    print("Number of timeseries files validated: {0}".format(len(series_files)))
    print("Number of files that passed test    : {0}".format(files_pass))
    print("Number of files that failed test    : {0}".format(files_fail))
    print("-"*43)
    # <<<<<<<<< Checking results

