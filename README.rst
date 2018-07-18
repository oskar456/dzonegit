Git hooks to manage a repository of DNS zones
=============================================

Main features
-------------

 - check if zone file compiles properly using `named-compilezone(8)`_
 - autodetect zone name from file name or ``$ORIGIN`` directive
 - enforce updating serial number when zone content is changed
 - both ``pre-commit`` and ``pre-receive``/``update`` hooks to enforce similar checks in the remote repository
 - ``post-receive`` hook to checkout the working copy from a bare repository, generate config snippets for various DNS server software and reload them
 - only Python standard library is used

.. _named-compilezone(8): https://linux.die.net/man/8/named-compilezone

Requirements
------------

 - Python 3.5+
 - `named-compilezone(8)`_ (part of BIND9 package)
 - git


Instalation and usage
---------------------

Please note that this project is not finished yet. Detailed instructions will follow later.
