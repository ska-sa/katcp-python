# test_katcp_bnf.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Test the KAT device communication language BNF.

   The message grammar is described in extended BNF where:

     * Optional items are enclosed in square brackets.
     * Items repeating 0 or more times are suffixed with a *.
     * Items repeating 1 or more times are suffixed with a +.
     * Items that may occur 0 or 1 times are suffixed with a ?.
     * Set difference is indicated by /.
     * Alternative choices in a production are separated by the | symbol.

    Grammar:

          <message> ::= <type> <name> <id> <arguments> <eol>
             <type> ::= "?" | "!" | "#"
             <name> ::= alpha (alpha | digit | "-")*
               <id> ::= "" | "[" digit+ "]"
       <whitespace> ::= (space | tab) [<whitespace>]
              <eol> ::= newline | carriage-return
        <arguments> ::= (<whitespace> <argument> <arguments>) | <whitespace> |
                        ""
         <argument> ::= (<plain> | <escape>)+
           <escape> ::= "\" <escapecode>
       <escapecode> ::= "\" | "_" | zero | "n" | "r" | "e" | "t" | "@"
          <special> ::= backslash | space | null | newline | carriage-return |
                        escape | tab
            <plain> ::= character / <special>

    Uses the ply library from http://www.dabeaz.com/ply/.
    """

from __future__ import division, print_function, absolute_import

import ply.lex as lex
import ply.yacc as yacc
import katcp
import unittest


class DclLexer(object):
    """Lexer definition for the DCL."""

    states = (
        ('argument', 'exclusive'),
    )

    tokens = (
        # any state
        'EOL',
        'WHITESPACE',
        # initial state
        'TYPE',
        'NAME',
        'ID',
        # argument
        'PLAIN',
        'ESCAPE',
    )

    t_ignore = ""

    # any state

    t_ANY_EOL = r'[\n\r]'

    def t_ANY_WHITESPACE(self, t):
        r'[ \t]+'
        t.lexer.begin("argument")
        return t

    # initial state

    t_TYPE = r'[?!#]'

    t_NAME = r'[a-zA-Z][a-zA-Z0-9\-]*'

    t_ID = r'\[[0-9]+\]'

    def t_error(self, t):
        """Error handler."""
        if t is None:
            raise katcp.KatcpSyntaxError("Syntax error.")
        else:
            raise katcp.KatcpSyntaxError("Invalid token: %s " % t.value)

    # argument state

    t_argument_PLAIN = r'[^ \t\e\n\r\\\0]'

    t_argument_ESCAPE = r'\\[\\_0nret@]'

    def t_argument_error(self, t):
        """Argument error handler."""
        if t is None:
            raise katcp.KatcpSyntaxError("Argument syntax error.")
        else:
            raise katcp.KatcpSyntaxError("Invalid argument token: %s " %
                                         t.value)


class DclGrammar(object):
    """Grammer definition for the DCL."""

    tokens = DclLexer.tokens

    def p_message(self, p):
        """message : TYPE NAME id arguments eol"""
        mtype = katcp.Message.TYPE_SYMBOL_LOOKUP[p[1]]
        name = p[2]
        mid = p[3]
        arguments = p[4]
        p[0] = katcp.Message(mtype, name, arguments, mid)

    def p_eol(self, p):
        """eol : EOL
               | empty"""
        pass

    def p_id(self, p):
        """id : ID
              | empty"""
        if p[1] is not None:
            # strip [] brackets
            p[0] = p[1][1:-1]
        else:
            p[0] = None

    def p_arguments(self, p):
        """arguments : WHITESPACE argument arguments
                     | WHITESPACE
                     | empty"""
        if len(p) == 4:
            p[0] = [p[2]] + p[3]
        else:
            # empty and whitespace productions
            p[0] = []

    def p_argument(self, p):
        """argument : argumentchar argument
                    | empty"""
        if len(p) == 3:
            p[0] = p[1] + p[2]
        else:
            # handle empty production
            p[0] = ""

    def p_argumentchar(self, p):
        """argumentchar : PLAIN
                        | ESCAPE"""
        if p[1][0] == "\\":
            cescape = p[1][1]
            p[0] = katcp.MessageParser.ESCAPE_LOOKUP[cescape]
        else:
            p[0] = p[1]

    def p_empty(self, p):
        """empty :"""
        pass

    def p_error(self, p):
        """Error handler."""
        # Note: this error handler should be unreachable because the
        # anything the lexer can tokenise should be parsable in our
        # case.
        raise katcp.KatcpSyntaxError("Parsing error (production: %r)." % (p,))


class Parser(object):
    """Wraps Lexer and Grammar Objects"""

    def __init__(self):
        self._lexer = lex.lex(object=DclLexer(), debug=0)
        self._parser = yacc.yacc(module=DclGrammar(), debug=0, write_tables=0)

    def parse(self, line):
        """Parse a line, return a Message."""

        self._lexer.begin("INITIAL")

        if line != '':
            m = self._parser.parse(line, lexer=self._lexer)
        else:
            # '' can cause the lexer to bomb out, so we avoid it
            m = self._parser.parse(' ', lexer=self._lexer)
        return m


class TestBnf(unittest.TestCase):
    """BNF tests."""

    def setUp(self):
        self.p = Parser()

    def test_simple_messages(self):
        """Simple tests of the parser."""

        m = self.p.parse("?foo\n")
        self.assertEqual(m.mtype, m.REQUEST)
        self.assertEqual(m.name, "foo")

        m = self.p.parse("!foz baz")
        self.assertEqual(m.mtype, m.REPLY)
        self.assertEqual(m.name, "foz")
        self.assertEqual(m.arguments, ["baz"])

        m = self.p.parse("#foz baz b")
        self.assertEqual(m.mtype, m.INFORM)
        self.assertEqual(m.name, "foz")
        self.assertEqual(m.arguments, ["baz", "b"])

    def test_escape_sequences(self):
        """Test escape sequences."""
        m = self.p.parse(r"?foo \\\_\0\n\r\e\t")
        self.assertEqual(m.arguments, ["\\ \0\n\r\x1b\t"])

    def test_lexer_errors(self):
        """Test errors which should be raised by the lexer."""
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, "")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, "^foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse,
                          "!foo tab\0arg")

    def test_empty_params(self):
        """Test parsing messages with empty parameters."""
        m = self.p.parse("!foo \@")  # 1 empty parameter
        self.assertEqual(m.arguments, [""])
        m = self.p.parse("!foo \@ \@")  # 2 empty parameter
        self.assertEqual(m.arguments, ["", ""])
        m = self.p.parse("!foo \_  \_  \@")  # space, space, \@
        self.assertEqual(m.arguments, [" ", " ", ""])

    def test_extra_whitespace(self):
        """Test extra whitespace around parameters."""
        m = self.p.parse("!foo \t\@  ")  # 1 empty parameter
        self.assertEqual(m.arguments, [""])
        m = self.p.parse("!foo   \@    \@")  # 2 empty parameter
        self.assertEqual(m.arguments, ["", ""])
        m = self.p.parse("!foo \_  \t\t\_\t  \@\t")  # space, space, \@
        self.assertEqual(m.arguments, [" ", " ", ""])

    def test_formfeed(self):
        """Test that form feeds are not treated as whitespace."""
        m = self.p.parse("!baz \fa\fb\f")
        self.assertEqual(m.arguments, ["\fa\fb\f"])

    def test_message_ids(self):
        """Test that messages with message ids are parsed as expected."""
        m = self.p.parse("?bar[123]")
        self.assertEqual(m.mtype, m.REQUEST)
        self.assertEqual(m.name, "bar")
        self.assertEqual(m.arguments, [])
        self.assertEqual(m.mid, "123")

        m = self.p.parse("!baz[1234] a b c")
        self.assertEqual(m.mtype, m.REPLY)
        self.assertEqual(m.name, "baz")
        self.assertEqual(m.arguments, ["a", "b", "c"])
        self.assertEqual(m.mid, "1234")
