"""Generate Check instances from ChecksXML, embed in a NagiosConfig file,
and extract them to execute the checks."""

import sys
import os
import time
import logging

logger = logging.getLogger(__name__)

from vardb import VariableList
from Checks import ChecksXML
from Checks import Check
from NagiosConfig import NagiosConfig
from NagiosCommands import NagiosCommands
from vardb import DataStore
from optparse import OptionValueError
import iss.time_tools as tt
import iss.nagios as nagios
from optparse import OptionParser

# The goal is to store Check instances, with all their associated
# parameters, so they can be loaded and executed over and over without
# repeating all the metadata lookup.  It just sounds more robust to me to
# run the checks from one config file, without depending upon all the
# metadata sources on each run.  It will also make it easier to tell when
# the config has changed and should be regenerated.

# So the way we're going to try it is to store the checks as xml inside
# comments in the nagios config file.

# For each check, write the xml serliazation in a comment, then follow it
# with the nagios service definition.

class NagiosQC(object):

    _embedded_prefix = "#CHECK: "

    def __init__(self):
        self.options = None
        self.hostname = 'RAF'
        self.args = None
        self.script_path = None
        self.operation = None

    def addOptions(self, parser):
        parser.add_option("--db", type="string", help="""\
Specify the database connection or hostname.  The default is 'acserver'.
Use 'c130' or 'gv' to use the ground real-time database for that plane.
Use 'env' to use the settings in the PG environment variables.""")

        parser.add_option("--projdir", type="string", help="""\
Specify or override the PROJ_DIR environment variable, the directory path
which contains project configuration directories.""")

        parser.add_option("--nagios", type="string", help="""\
The path to the nagios configuration file.  When generating a config,
the config will be written to this file.  When actually running the nagios
passive checks, the checks will be read from this file.  The default path
is %s.""" % (NagiosConfig.DEFAULT_PATH))

        parser.add_option("--checks", type="string", help="""\
Path to the XML checks.xml file.  Defaults to the project config directory.""")

        parser.add_option("--vdb", type="string", help="""\
Override the default path to the vardb.xml file.""")

        parser.add_option("--commands", type="string", help="""\
Path to which nagios commands will be written.  The default is %s, but
for debugging it can be a regular file or /dev/null.""" % 
                          (NagiosCommands.DefaultCommandFile), 
                          default=False)
        parser.add_option("--script", type="string", help="""\
Override the full script command line to save in the nagios config file.
By default the script check command uses the same options as when generating
the config file, replacing the 'config' operation with 'check', eg

   python <full-path-to>/nagiosqc.py check [original-arguments ...]

""")

        def callback_timestamp(option, opt_str, value, parser):
            try:
                parser.values.ensure_value(option.dest, tt.parseTime(value))
            except Exception, x:
                raise OptionValueError(str(x))

        parser.add_option("--timestamp", type="string", action="callback",
                          callback=callback_timestamp, help="""\
Assign the given timestamp as the time of the nagios passive check result.""")


    def setOptions(self, options, args):
        """
        Specify the options and the argument list for this run.

        The argument list includes the executable path.  The script path
        and arguments are saved so the script can be called later with the
        'check' operationo using the same arguments.
        """
        self.options = options
        # Derive the path to this script and the arguments.
        # As a special case, if the executable is empty, then derive
        # a path using this module's directory.
        self.script_path = args[0]
        if not self.script_path:
            self.script_path = os.path.join(os.path.dirname(__file__),
                                            "nagiosqc.py")
        self.script_path = os.path.abspath(self.script_path)
        self.args = args[1:]


    def parseOptions(self, argv, parser=None):
        if not parser:
            parser = OptionParser()
        self.addOptions(parser)

        # Copy off the argument list
        argsave = argv[:]
        (options, argv) = parser.parse_args(argv)

        # Remove the executable and all that should be left is the operation.
        del argv[0]
        if not argv or argv[0] not in ['config', 'check']:
            print("Operation must be 'config' or 'check'.")
            sys.exit(1)

        self.operation = argv[0]

        # Remove the operation name from the original arg list and pass
        # them to the NagiosQC instance so they can be preserved in the
        # config file.
        del argsave[argsave.index(self.operation)]
        self.setOptions(options, argsave)
        return options

    def run(self):
        if self.operation == "config":
            self.writeConfig()
        elif self.operation == "check":
            status = self.executeChecks()
            print(status.consoleMessage())
        else:
            print("Unknown operation: %s" % (self.operation))
            return 1
        return 0

    def getCheckCommand(self, script_path=None):
        "Generate the check command, optionally overriding the script path."
        if self.options.script:
            return self.options.script
        arglist = ""
        if self.args:
            arglist = " ".join(self.args)
        if not script_path:
            script_path = self.script_path
        return "python %s check %s" % (script_path, arglist)

    def writeConfigFile(self, checks, path=None):
        configfile = NagiosConfig()
        configfile.setCommandLine(self.getCheckCommand())
        configfile.open(path)
        configfile.writeOpening(self.hostname)
        configfile.write(configfile.makeHost(self.hostname))
        for check in checks:
            configfile.write("\n" + NagiosQC._embedded_prefix + 
                             check.toString() + "\n")
            svcdef = configfile.makeService(self.hostname, check.name())
            configfile.write(svcdef)
        configfile.close()

    def readChecksFromConfigFile(self, path=None):
        configfile = NagiosConfig()
        configfile.openForReading(path)
        checks = []
        for line in configfile.iterateLines(NagiosQC._embedded_prefix):
            line = line.replace(NagiosQC._embedded_prefix, "", 1)
            check = Check.fromString(line)
            checks.append(check)
        logger.info("extracted %d checks embedded in '%s'" %
                    (len(checks), configfile.path))
        configfile.close()
        return checks

    def setupVariableList(self):
        vlist = VariableList()
        if self.options.db:
            vlist.setDatabaseSpecifier(self.options.db)
        if self.options.projdir:
            vlist.projdir = self.options.projdir
        if self.options.vdb:
            vlist.vdbpath = self.options.vdb
        vlist.loadVariables()
        return vlist

    def getProjDir(self):
        projdir = self.options.projdir
        if not projdir:
            projdir = os.environ.get("PROJ_DIR")
        if not projdir:
            projdir = "/home/local/projects"
        return projdir

    def _locateChecksXML(self, vlist):
        """
        Derive the location of the XML file with the check templates.
        """
        # If an explicit checks path is in the options, then it overrides
        # everything, but it can also be relative to the project directory.
        path = self.options.checks

        # If the path does not exist, try appending it to the project
        # directory.
        projdir = self.getProjDir()
        if projdir and path and not os.path.exists(path):
            rpath = os.path.join(projdir, path)
            if os.path.exists(rpath):
                path = rpath

        # Otherwise the path comes from the project and aircraft in the
        # database using the default name.
        if not path:
            path = os.path.join(vlist.configPath(), 'checks.xml')

        # We could derive a path by expanding the PROJECT and AIRCRAFT
        # environment variables here, but what would be the point?  The
        # checks being generated must be consistent with the database
        # regardless of the environment settings.
        # path = os.path.expandvars(
        #                 '${PROJ_DIR}/${PROJECT}/${AIRCRAFT}/checks.xml')

        return path

    def writeConfig(self):
        vlist = self.setupVariableList()
        # Merge in the vardb.xml in case any checks need to inherit limits
        # from the metadata.
        vlist.loadVdbFile()
        xmlpath = self._locateChecksXML(vlist)

        # Load the checks.xml file and instantiate all the checks against
        # the current variables.
        checksxml = ChecksXML(xmlpath)
        checksxml.load()
        checks = checksxml.generateChecks(vlist.getVariables())
        self.writeConfigFile(checks, self.options.nagios)
        vlist.close()

    def executeChecks(self):
        qcstatus = nagios.Status("nagiosqc")
        begin = time.time()

        # Extract and parse the checks from the nagios config file and
        # execute them.
        checks = self.readChecksFromConfigFile(self.options.nagios)
        
        # Compile a list of variables required with the maximum lookback
        # required for each.
        vlist = self.setupVariableList()
        lookback = 1
        for c in checks:
            vlist.selectVariable(c.vname)
            lookback = max(lookback, c.lookback)

        # Now request data values for the select variables.  What we get
        # back is a map from variable name to a list of values.
        datastore = vlist.getLatestValues(DataStore(), lookback)

        # For each check, make sure values were retrieved (meaning the
        # variable is in the database) before calling the check.  If checks
        # ever depend on more than one variable, then we'll probably need a
        # syntax for that in the CheckTemplate and Check (such as CSV) so
        # we can still aggregate the variables on which the checks depend,
        # otherwise it would be up to each check to make sure it's
        # variables exist in the datastore.
        results = []
        nvars = 0
        nfail = 0
        nchecks = len(checks)
        for c in checks:
            if not datastore.getValues(c.vname):
                status = c.newStatus().critical(
                    "Variable '%s' is not in the database." % (c.vname))
            else:
                status = c.check(datastore)
                nvars += 1
            nfail += int(not status.is_ok())
            results.append(status)

        # Now we can pipe the results to the right place.
        #cmds is passive check output file, which is piped to nagios by shell
        commands = NagiosCommands()
        commands.open(self.options.commands)
        when = self.options.timestamp
        if when:
            logger.debug("Submitting check results at time: %s" %
                         (tt.formatTime(when)))
        for status in results:
            statline = "%s;%s;%d;%s" % (self.hostname, status.getName(), 
                                        status.getLevel(), 
                                        status.consoleMessage())
            commands.processServiceCheckResult(statline, when)
        commands.close()
        vlist.close()
        end = time.time()
        qcstatus.ok("%d variables for %d checks, %d not ok, "
                    "elapsed time: %s" %
                    (nvars, nchecks, nfail, tt.formatInterval(end - begin)))
        return qcstatus
