# -*- python -*-

import os
import sys

sys.path.append('site_scons')
import eol_scons 

env = Environment(tools=['default', 'jlocal', 'pylint',
                         'testing', 'postgres_testdb'])

sources = Split("""
nagiosqc.py
Checks.py
NagiosCommands.py
NagiosQC.py
NagiosConfig.py
""")

tests = Split("test_nagios_qc.py")

# Test against the python packages in the source tree
env.AppendENVPath('PYTHONPATH', "#/../python")
env.AppendENVPath('PYTHONPATH', "#/../vardb/python")
env.AppendENVPath('PYTHONPATH', env.Dir('.').get_abspath())

env.PythonLint('lint', sources, PYLINTPYTHONPATH=env['ENV']['PYTHONPATH'])

runtest = env.TestRun('pytests', sources + tests, "py.test ${SOURCES}")

sources = Split("Checks.py NagiosConfig.py nagiosqc.py")

pg = env.PostgresTestDB()

wqc = env.Command(['winter-nagios-qc.cfg', 'winter-results.txt'],
                  ['nagiosqc.py', 'WINTER-rf03-real-time-acserver.sql', 
                   'winter_vardb.xml', 'winter_checks.xml'] + sources,
                  [ pg.action_init,
                    "${SOURCE.abspath} --debug "
                    "--script "
                    "'python /home/local/raf/nagios-qc/nagiosqc.py check' "
                    "--db env --checks winter_checks.xml "
                    "--vdb winter_vardb.xml --nagios ${TARGETS[0]} config",
                    "${SOURCE.abspath} --debug --timestamp 20150228012345 "
                    "--db env --nagios ${TARGETS[0]} "
                    "--commands ${TARGETS[1]} check",
                    pg.action_destroy ], chdir=1)

env.AlwaysBuild(wqc)
env.Alias('test', wqc)

env.Alias('diff', 
          env.Command("diff", ["expected/winter-nagios-qc.cfg", wqc[0] ],
                      "diff -c ${SOURCES}"))
env.Alias('test', 'diff')
env.Alias('cdiff', 
          env.Command("cdiff", ["expected/winter-results.txt", wqc[1] ],
                      "diff -c ${SOURCES}"))
env.Alias('test', 'cdiff')
