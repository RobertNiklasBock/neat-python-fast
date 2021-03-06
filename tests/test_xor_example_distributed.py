from __future__ import print_function
import multiprocessing
import os
import random
import sys
import platform
import time
import unittest

import neat
from neatfast.distributed import MODE_PRIMARY, MODE_SECONDARY

ON_PYPY = platform.python_implementation().upper().startswith("PYPY")

# 2-input XOR inputs and expected outputs.
XOR_INPUTS = [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)]
XOR_OUTPUTS = [(0.0,), (1.0,), (1.0,), (0.0,)]

def eval_genome_distributed(genome, config):
    fitness = 1.0
    net = neatfast.nn.FeedForwardNetwork.create(genome, config)
    for xi, xo in zip(XOR_INPUTS, XOR_OUTPUTS):
        output = net.activate(xi)
        fitness -= (output[0] - xo[0]) ** 2
    return fitness

def run_primary(addr, authkey, generations):
    # Determine path to configuration file. This path manipulation is
    # here so that the script will run successfully regardless of the
    # current working directory.
    local_dir = os.path.dirname(__file__)
    config_path = os.path.join(local_dir, 'test_configuration2')

    # Load configuration.
    config = neatfast.Config(neatfast.DefaultGenome, neatfast.DefaultReproduction,
                         neatfast.DefaultSpeciesSet, neatfast.DefaultStagnation,
                         config_path)

    # Create the population, which is the top-level object for a NEAT run.
    p = neatfast.Population(config)

    # Add a stdout reporter to show progress in the terminal.
    p.add_reporter(neatfast.StdOutReporter(True))
    stats = neatfast.StatisticsReporter()
    p.add_reporter(stats)
    checkpointer = neatfast.Checkpointer(max(1,int(generations/4)), 10)
    p.add_reporter(checkpointer)

    # Run for the specified number of generations.
    winner = None
    de = neatfast.DistributedEvaluator(
        addr,
        authkey=authkey,
        eval_function=eval_genome_distributed,
        secondary_chunksize=15,
        mode=MODE_PRIMARY,
        )
    de.start()
    winner = p.run(de.evaluate, generations)
    print("===== stopping DistributedEvaluator =====")
    de.stop(wait=3, shutdown=True, force_secondary_shutdown=False)

    if winner:
        # Display the winning genome.
        print('\nBest genome:\n{!s}'.format(winner))

        # Show output of the most fit genome against training data.
        print('\nOutput:')
        winner_net = neatfast.nn.FeedForwardNetwork.create(winner, config)
        for xi, xo in zip(XOR_INPUTS, XOR_OUTPUTS):
            output = winner_net.activate(xi)
            print("input {!r}, expected output {!r}, got {!r}".format(xi, xo, output))

    if (checkpointer.last_generation_checkpoint >= 0) and (checkpointer.last_generation_checkpoint < 100):
        filename = 'neat-checkpoint-{0}'.format(checkpointer.last_generation_checkpoint)
        print("Restoring from {!s}".format(filename))
        p2 = neatfast.checkpoint.Checkpointer.restore_checkpoint(filename)
        p2.add_reporter(neatfast.StdOutReporter(True))
        stats2 = neatfast.StatisticsReporter()
        p2.add_reporter(stats2)

        winner2 = None
        time.sleep(3)
        de.start()
        winner2 = p2.run(de.evaluate, (100-checkpointer.last_generation_checkpoint))
        print ("===== stopping DistributedEvaluator (forced) =====")
        de.stop(wait=3, shutdown=True, force_secondary_shutdown=True)

        if winner2:
            if not winner:
                raise Exception("Had winner2 without first-try winner")
        elif winner:
            raise Exception("Had first-try winner without winner2")

def run_secondary(addr, authkey, num_workers=1):
    # Determine path to configuration file. This path manipulation is
    # here so that the script will run successfully regardless of the
    # current working directory.
    local_dir = os.path.dirname(__file__)
    config_path = os.path.join(local_dir, 'test_configuration2')

    # Load configuration.
    config = neatfast.Config(neatfast.DefaultGenome, neatfast.DefaultReproduction,
                         neatfast.DefaultSpeciesSet, neatfast.DefaultStagnation,
                         config_path)

    # Create the population, which is the top-level object for a NEAT run.
    p = neatfast.Population(config)

    # Add a stdout reporter to show progress in the terminal.
    p.add_reporter(neatfast.StdOutReporter(True))
    stats = neatfast.StatisticsReporter()
    p.add_reporter(stats)

    # Run for the specified number of generations.
    de = neatfast.DistributedEvaluator(
        addr,
        authkey=authkey,
        eval_function=eval_genome_distributed,
        mode=MODE_SECONDARY,
        num_workers=num_workers,
        )
    try:
        de.start(secondary_wait=3, exit_on_stop=True, reconnect=True)
    except SystemExit:
        pass
    else:
        raise Exception("DistributedEvaluator in secondary mode did not try to exit!")


@unittest.skipIf(ON_PYPY, "This test fails on pypy during travis builds (frequently due to timeouts) but usually works locally.")
def test_xor_example_distributed():
    """
    Test to make sure restoration after checkpoint works with distributed.
    """

    addr = ("localhost", random.randint(12000, 30000))
    authkey = b"abcd1234"
    mp = multiprocessing.Process(
        name="Primary evaluation process",
        target=run_primary,
        args=(addr, authkey, 100),
        )
    mp.start()
    swcp = multiprocessing.Process(
        name="Child evaluation process (direct evaluation)",
        target=run_secondary,
        args=(addr, authkey, 1),
        )
    swcp.daemon = True  # we cannot set this if using multiple worker processes
    swcp.start()
    mp.join()
    if mp.exitcode != 0:
        raise Exception("Primary-process exited with status {s}!".format(s=mp.exitcode))
    time.sleep(3)
    if swcp.is_alive():
        print("Secondary process (pid {!r}) still alive".format(swcp.pid), file=sys.stderr)
    swcp.join()
    if swcp.exitcode != 0:
        raise Exception("Singleworker-secondary-process exited with status {s}!".format(s=swcp.exitcode))



if __name__ == '__main__':
    test_xor_example_distributed()

