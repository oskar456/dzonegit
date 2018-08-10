Git hooks to manage a repository of DNS zones
=============================================

``dzonegit`` is a set of Git hooks allowing you to manage DNS zone files in a
git repository. First, zone file sanity checks are run by ``pre-commit`` hook
on your computer. After pushing changes to a bare repository on the DNS server,
the sanity checks are run again on the server and if everything is OK,
repository is checked out to a directory, DNS software configuration
snippets are re-generated from a simple template and finally reload command
is issued.

Main features
-------------

- check if zone file compiles properly using `named-compilezone(8)`_
- autodetect zone name from file name or ``$ORIGIN`` directive
- enforce updating serial number when zone content is changed
- both ``pre-commit`` and ``pre-receive``/``update`` hooks to enforce similar checks in the remote repository
- ``post-receive`` hook to checkout the working copy from a bare repository, generate config snippets for various DNS server software and reload them
- only Python standard library is used


Requirements
------------

- Python 3.5+
- `named-compilezone(8)`_ (part of `bind9utils` package)
- git


Instalation and usage
---------------------

- install required dependencies
- install ``dzonegit`` package using your favourite tool (``virtualenvwrapper``,
  ``venv``, ``pipenv``, etc.)
- in the local repository, create a symlink for the ``pre-commit`` hook:

  ``$ ln -s $(which dzonegit-pre-commit) /path/to/repo/.git/hooks/pre-commit``
- on the server, install some git repository management software, preferrably Gitolite_
- on the server, install either ``pre-receive`` or ``update`` hook (both do the same) as
  well as ``post-receive`` hook. See `Gitolite documentation on how to add custom hooks`_
- on the server set up the configuration options for each repository

Configuration options
---------------------

All configuration options are stored in `git-config(1)`_ in section named ``dzonegit``.
All boolean options default to *False*.


*dzonegit.ignorewhitespaceerrors*
  Ignore white space errors in ``pre-commit`` and ``pre-receive``/``update`` hooks.

*dzonegit.noserialupdate*
  Do not try to automatically update zone serial number if necessary.
  Valid only in the ``pre-commit`` hook.

*dzonegit.checkoutpath*
  Path to a writable directory, to which ``post-receive`` hook checks out
  current *HEAD* after each update.

*dzonegit.conffiletemplate*
  Path to a JSON file containing template for generating DNS server
  configuration snippet. See below for file format specification. More
  files can be provided by appending single digit from 1 to 9 to this option.

*dzonegit.conffilepath*
  Path to a writable file to generate DNS server configuration snippet.
  More files can be provided by appending single digit from 1 to 9 to this
  option. Each file is generated using the template with corresponding suffix.

*dzonegit.reconfigcmd*
  A command to run when zones are introduced, deleted or renamed in the
  repository. Should do something like ``rndc reconfig``. More commands
  can be provided by appending single digit from 1 to 9 to this option.

*dzonegit.zonereloadcmd*
  A command to run for each zone, whose zone file has been modified. Zone
  name is automatically appended as the last argument. Should do something
  like ``rndc reload``. More commands can be provided by appending single digit
  from 1 to 9 to this option.

*dzonegit.zoneblacklist*
  Path to a text file containing list of zone names without trailing dots,
  one per line. If zone is found on the blacklist, it is ignored when
  ``post-receive`` hook generates configuration. Wildcards can be used as
  well, see `JSON template`_ below.

*dzonegit.zonewhitelist*
  Path to a text file containing list of zone names without trailing dots,
  one per line. If not empty and zone is not found on the whitelist,
  it is ignored when ``post-receive`` hook generates configuration. Wildcards
  can be used as well, see `JSON template`_ below.

JSON template
-------------

The DNS server configuration snippets are generated using a simple JSON-based
template. All keys are optional but please make sure the file is a valid JSON
file. It is possible to define a zone-specific options, for instance for
changing DNSSEC parameters per zone. Those zone-specific options allow usage of
wildcards; if an exact match of zone name is not found, the leftmost label is
substituted with `*`. If still no match is found, the leftmost label is dropped
and the second one is again substituted with `*`. In the end, a single `*` is
checked. Only if even this key is not found, the value of *defaultvar* is used
as the zone-specific option.

Valid keys are:

*header*
  A string that is templated to the begining of the output file.

*footer*
  A string that is templated to the end of the output file.

*item*
  A string that is templated for each zone.

*defaultvar*
  A string that would template variable ``$zonevar`` expand to if there is not
  a zone-specific variable defined, nor any wildcard matched.

*zonevars*
  An object mapping zone names (without the final dot) to a zone-specific
  variable to which template variable ``$zonevar`` would expand to. Using
  wildcards is possible by replacing the leftmost label with `*`. Ultimately,
  a key with label `*` will match every single zone (making *defaultvar*
  option litte bit pointless)

In the template strings, these placeholders are supported:

``$datetime``
  Current timestamp

``$zonename``
  Zone name, without trailing dot

``$zonefile``
  Full path to the zone file

``$zonevar``
  Per-zone specific variable, see above

Example JSON template for Knot DNS
..................................

.. code-block:: json

    {
      "header": "# Managed by dzonegit, do not edit.\nzone:",
      "footer": "",
      "item": " - domain: \"$zonename\"\n   file: \"$zonefile\"\n   $zonevar\n",
      "defaultvar": "template: default",
      "zonevars": {
        "example.com": "template: signed",
        "*.cz": "template: czdomains",
        "*.in-addr.arpa": "template: ipv4reverse"
      }
    }


Example JSON template for BIND
..............................

.. code-block:: json

    {
      "header": "# Autogenerated by dzonegit on $datetime. Do not edit.\n",
      "item": "zone \"$zonename\" {\n type master;\n file \"$zonefile\";\n};"
    }


.. _named-compilezone(8): https://linux.die.net/man/8/named-compilezone
.. _git-config(1): https://linux.die.net/man/1/git-config
.. _Gitolite: http://gitolite.com/gitolite/index.html
.. _Gitolite documentation on how to add custom hooks: http://gitolite.com/gitolite/cookbook/#hooks
