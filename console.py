"""
Jython Console with Code Completion

This uses the basic Jython Interactive Interpreter.
The UI uses code from Carlos Quiroz's 'Jython Interpreter for JEdit' http://www.jedit.org
"""

from javax.swing import JFrame, JScrollPane, JWindow, JTextPane, Action, KeyStroke, WindowConstants
from javax.swing.text import JTextComponent, TextAction, SimpleAttributeSet, StyleConstants, DefaultEditorKit
from java.awt import Color, Font, FontMetrics, Point
from java.awt.event import  InputEvent, KeyEvent, WindowAdapter

import jintrospect
from jintrospect import debug
from popup import Popup
from tip import Tip
from history import History

import os
import sys
import traceback
from code import InteractiveInterpreter
from org.python.util import InteractiveConsole

__author__ = "Don Coleman <dcoleman@chariotsolutions.com>"

import re
# allows multiple imports like "from java.lang import String, Properties"
_re_from_import = re.compile("from\s+\S+\s+import(\s+\S+,\s?)?")

class Console:
    PROMPT = sys.ps1
    PROCESS = sys.ps2
    BANNER = ["Jython Completion Shell", InteractiveConsole.getDefaultBanner()]

    def __init__(self):

        self.history = History(self)

        self.buffer = [] # buffer for multi-line commands        
        self.locals = {}

        self.interp = Interpreter(self, self.locals)
        sys.stdout = StdOutRedirector(self)

        self.text_pane = JTextPane(keyTyped = self.keyTyped, keyPressed = self.keyPressed)

        os_name = os.path.System.getProperty("os.name")
        if os_name.startswith("Win"):
            exit_key = KeyEvent.VK_Z
        else:
            exit_key = KeyEvent.VK_D

        keyBindings = [
            (KeyEvent.VK_ENTER, 0, "jython.enter", self.enter),
            (KeyEvent.VK_DELETE, 0, "jython.delete", self.delete),
            (KeyEvent.VK_HOME, 0, "jython.home", self.home),
            (KeyEvent.VK_LEFT, InputEvent.META_DOWN_MASK, "jython.home", self.home),                 
            (KeyEvent.VK_UP, 0, "jython.up", self.history.historyUp),
            (KeyEvent.VK_DOWN, 0, "jython.down", self.history.historyDown),
            (KeyEvent.VK_PERIOD, 0, "jython.showPopup", self.showPopup),
            (KeyEvent.VK_ESCAPE, 0, "jython.hide", self.hide),                   
            
            ('(', 0, "jython.showTip", self.showTip),
            (')', 0, "jython.hideTip", self.hideTip),
            (exit_key, InputEvent.CTRL_MASK, "jython.exit", self.quit),   
            (KeyEvent.VK_SPACE, InputEvent.CTRL_MASK, "jython.showPopup", self.showPopup),    
            (KeyEvent.VK_SPACE, 0, "jython.space", self.spaceTyped),   

            # TODO
            #(KeyEvent.VK_BACK_SPACE, 0, "jython.backspace", self.backSpaceTyped),
            #(KeyEvent.VK_LEFT, 0, "jython.leftArrow", self.backSpaceTyped),                              

            # Mac/Emacs keystrokes
            (KeyEvent.VK_A, InputEvent.CTRL_MASK, "jython.home", self.home),
            (KeyEvent.VK_E, InputEvent.CTRL_MASK, "jython.end", self.end),                        
            (KeyEvent.VK_K, InputEvent.CTRL_MASK, "jython.killToEndLine", self.killToEndLine),
            (KeyEvent.VK_Y, InputEvent.CTRL_MASK, "jython.paste", self.paste),

            
            # TODO CTRL/COMMAND + UP and DOWN should be mapped to normal up and down arrow functions
            #(KeyEvent.VK_UP, InputEvent.CTRL_MASK, DefaultEditorKit.upAction, self.text_pane.keymap.getAction(KeyStroke.getKeyStroke(KeyEvent.VK_UP, 0))),
            #(KeyEvent.VK_DOWN, InputEvent.CTRL_MASK, DefaultEditorKit.downAction, self.text_pane.keymap.getAction(KeyStroke.getKeyStroke(KeyEvent.VK_DOWN, 0)))
            ]

        keymap = JTextComponent.addKeymap("jython", self.text_pane.keymap)
        for (key, modifier, name, function) in keyBindings:
            keymap.addActionForKeyStroke(KeyStroke.getKeyStroke(key, modifier), ActionDelegator(name, function))        
        self.text_pane.keymap = keymap
                
        self.doc = self.text_pane.document
        self.__propertiesChanged()
        self.__inittext()
        self.initialLocation = self.doc.createPosition(self.doc.length-1)

        # Don't pass frame to popups. JWindows with null owners are not focusable
        # this fixes the focus problem on Win32, but make the mouse problem worse
        self.popup = Popup(None, self.text_pane)
        self.tip = Tip(None)

        # get fontmetrics info so we can position the popup
        metrics = self.text_pane.getFontMetrics(self.text_pane.getFont())
        self.dotWidth = metrics.charWidth('.')
        self.textHeight = metrics.getHeight()

        # add some handles to our objects
        self.locals['console'] = self

    def insertText(self, text):
        """insert text at the current caret position"""
        # seems like there should be a better way to do this....
        # might be better as a method on the text component?
        caretPosition = self.text_pane.getCaretPosition()
        self.text_pane.select(caretPosition, caretPosition)
        self.text_pane.replaceSelection(text)
        self.text_pane.setCaretPosition(caretPosition + len(text))

    def getText(self):
        """get text from last line of console"""
        offsets = self.__lastLine()
        text = self.doc.getText(offsets[0], offsets[1]-offsets[0])
        return text.rstrip()

    def getDisplayPoint(self):
        """Get the point where the popup window should be displayed"""
        screenPoint = self.text_pane.getLocationOnScreen()
        caretPoint = self.text_pane.caret.getMagicCaretPosition()
        # BUG: sometimes caretPoint is None
        # To duplicate type "java.aw" and hit '.' to complete selection while popup is visible

        x = screenPoint.getX() + caretPoint.getX() + self.dotWidth
        y = screenPoint.getY() + caretPoint.getY() + self.textHeight
        return Point(int(x),int(y))

    def hide(self, event=None):
        """Hide the popup or tip window if visible"""
        if self.popup.visible:
            self.popup.hide()
        if self.tip.visible:
            self.tip.hide()

    def hideTip(self, event=None):
        self.tip.hide()
        self.insertText(')')

    def showTip(self, event=None):
        # get the display point before writing text
        # otherwise magicCaretPosition is None
        displayPoint = self.getDisplayPoint()

        if self.popup.visible:
            self.popup.hide()
        
        line = self.getText()

        # introspect is expecting a trailing '('
        line += '('

        self.insertText('(')
        
        (name, argspec, tip) = jintrospect.getCallTipJava(line, self.locals)

        if tip:
            self.tip.setLocation(displayPoint)
            self.tip.setText(tip)
            self.tip.show()
            
    def showPopup(self, event=None):
        """show code completion popup"""
        line = self.getText()

        # this is silly, I have to add the '.' and the other code removes it.
        line = line + '.'
        # TODO get this code into Popup
        # TODO handle errors gracefully
        try:
            list = jintrospect.getAutoCompleteList(line, self.locals)
        except Exception, e:
            print >> sys.stderr, e
            return

        if len(list) == 0:
            #print >> sys.stderr, "list was empty"
            return

        self.popup.setLocation(self.getDisplayPoint())

        self.popup.setMethods(list)
        self.popup.show()
        self.popup.list.setSelectedIndex(0)

    def inLastLine(self, include = 1):
        """ Determines whether the cursor is in the last line """
        limits = self.__lastLine()
        caret = self.text_pane.caretPosition
        if self.text_pane.selectedText:
            caret = self.text_pane.selectionStart
        if include:
            return (caret >= limits[0] and caret <= limits[1])
        else:
            return (caret > limits[0] and caret <= limits[1])

    def enter(self, event):
        """ Triggered when enter is pressed """
        text = self.getText()
        self.buffer.append(text)
        source = "\n".join(self.buffer)
        more = self.interp.runsource(source)
        if more:
            self.printOnProcess()
        else:
            self.resetbuffer()
            self.printPrompt()
        self.history.append(text)

        self.hide()

    def quit(self, event=None):
        sys.exit()

    def resetbuffer(self):
        self.buffer = []

    def home(self, event):
        """ Triggered when HOME is pressed """
        if self.inLastLine():
            # go to end of PROMPT
            self.text_pane.caretPosition = self.__lastLine()[0]
        else:
            lines = self.doc.rootElements[0].elementCount
            for i in xrange(0,lines-1):
                offsets = (self.doc.rootElements[0].getElement(i).startOffset, \
                    self.doc.rootElements[0].getElement(i).endOffset)
                line = self.doc.getText(offsets[0], offsets[1]-offsets[0])
                if self.text_pane.caretPosition >= offsets[0] and \
                    self.text_pane.caretPosition <= offsets[1]:
                    if line.startswith(Console.PROMPT) or line.startswith(Console.PROCESS):
                        self.text_pane.caretPosition = offsets[0] + len(Console.PROMPT)
                    else:
                        self.text_pane.caretPosition = offsets[0]

    def end(self, event):
        if self.inLastLine():
            self.text_pane.caretPosition = self.__lastLine()[1] - 1

    # TODO look using text_pane replace selection like self.insertText
    def replaceRow(self, text):
        """ Replaces the last line of the textarea with text """
        offset = self.__lastLine()
        last = self.doc.getText(offset[0], offset[1]-offset[0])
        if last != "\n":
            self.doc.remove(offset[0], offset[1]-offset[0]-1)
        self.__addOutput(self.infoColor, text)
             
    def delete(self, event):
        """ Intercepts delete events only allowing it to work in the last line """
        if self.inLastLine():
            if self.text_pane.selectedText:
                self.doc.remove(self.text_pane.selectionStart, self.text_pane.selectionEnd - self.text_pane.selectionStart)
            elif self.text_pane.caretPosition < self.doc.length:
                self.doc.remove(self.text_pane.caretPosition, 1)

    def backSpaceListener(self, event=None):
        """ Don't allow backspace or left arrow to go over prompt """
        if self.text_pane.getCaretPosition() <= self.__lastLine()[0]:
            event.consume()
                                       
    def spaceTyped(self, event=None):
        """check we we should complete on the space key"""
        matches = _re_from_import.match(self.getText())
        if matches:
            self.showPopup()

    def killToEndLine(self, event=None):
        if self.inLastLine():
            caretPosition = self.text_pane.getCaretPosition()
            self.text_pane.setSelectionStart(caretPosition)
            self.text_pane.setSelectionEnd(self.__lastLine()[1] - 1)
            self.text_pane.cut()

    def paste(self, event=None):
        if self.inLastLine():
            self.text_pane.paste()

    def keyTyped(self, event):
        #print >> sys.stderr, "keyTyped", event.getKeyCode()
        if not self.inLastLine():
            event.consume()

    def keyPressed(self, event):
        if self.popup.visible:
            self.popup.key(event)
        #print >> sys.stderr, "keyPressed", event.getKeyCode()
        if event.keyCode == KeyEvent.VK_BACK_SPACE or event.keyCode == KeyEvent.VK_LEFT:
            self.backSpaceListener(event)
                
    # TODO refactor me
    def write(self, text):
        self.__addOutput(self.infoColor, text)

    def printResult(self, msg):
        """ Prints the results of an operation """
        self.__addOutput(self.text_pane.foreground, "\n" + str(msg))

    def printError(self, msg): 
        self.__addOutput(self.errorColor, "\n" + str(msg))

    def printOnProcess(self):
        """ Prints the process symbol """
        self.__addOutput(self.infoColor, "\n" + Console.PROCESS)

    def printPrompt(self):
        """ Prints the prompt """
        self.__addOutput(self.infoColor, "\n" + Console.PROMPT)
        
    def __addOutput(self, color, msg):
        """ Adds the output to the text area using a given color """
        from javax.swing.text import BadLocationException
        style = SimpleAttributeSet()

        if color:
            style.addAttribute(StyleConstants.Foreground, color)

        self.doc.insertString(self.doc.length, msg, style)
        self.text_pane.caretPosition = self.doc.length

    def __propertiesChanged(self):
        """ Detects when the properties have changed """
        self.text_pane.background = Color.white #jEdit.getColorProperty("jython.bgColor")
        self.text_pane.foreground = Color.blue #jEdit.getColorProperty("jython.resultColor")
        self.infoColor = Color.black #jEdit.getColorProperty("jython.textColor")
        self.errorColor = Color.red # jEdit.getColorProperty("jython.errorColor")

        family = "Monospaced" # jEdit.getProperty("jython.font", "Monospaced")
        size = 14 #jEdit.getIntegerProperty("jython.fontsize", 14)
        style = Font.PLAIN #jEdit.getIntegerProperty("jython.fontstyle", Font.PLAIN)
        self.text_pane.setFont(Font(family,style,size))

    def __inittext(self):
        """ Inserts the initial text with the jython banner """
        self.doc.remove(0, self.doc.length)
        for line in "\n".join(Console.BANNER):
            self.__addOutput(self.infoColor, line)
        self.printPrompt()
        self.text_pane.requestFocus()

    def __lastLine(self):
        """ Returns the char offests of the last line """
        lines = self.doc.rootElements[0].elementCount
        offsets = (self.doc.rootElements[0].getElement(lines-1).startOffset, \
                   self.doc.rootElements[0].getElement(lines-1).endOffset)
        line = self.doc.getText(offsets[0], offsets[1]-offsets[0])
        if len(line) >= 4 and (line[0:4]==Console.PROMPT or line[0:4]==Console.PROCESS):
            return (offsets[0] + len(Console.PROMPT), offsets[1])
        return offsets


class ActionDelegator(TextAction):
    """
        Class action delegator encapsulates a TextAction delegating the action
        event to a simple function
    """
    def __init__(self, name, delegate):
        TextAction.__init__(self, name)
        self.delegate = delegate

    def actionPerformed(self, event):
        if isinstance(self.delegate, Action):
            self.delegate.actionPerformed(event)
        else:
            self.delegate(event)

class Interpreter(InteractiveInterpreter):
    def __init__(self, console, locals):
        InteractiveInterpreter.__init__(self, locals)
        self.console = console
        
    def write(self, data):
        # send all output to the textpane
        # KLUDGE remove trailing linefeed
        self.console.printError(data[:-1])
        
# redirect stdout to the textpane
class StdOutRedirector:
    def __init__(self, console):
        self.console = console
        
    def write(self, data):
        #print >> sys.stderr, ">>%s<<" % data
        if data != '\n':
            # This is a sucky hack.  Fix printResult
            self.console.printResult(data)

class JythonFrame(JFrame):
    def __init__(self):
        self.title = "Jython"
        self.size = (600, 400)
        try:
            self.setDefaultCloseOperation(WindowConstants.EXIT_ON_CLOSE)
        except:
            # assume jdk < 1.4
            self.addWindowListener(KillListener())
            self.setDefaultCloseOperation(WindowConstants.DISPOSE_ON_CLOSE)

class KillListener(WindowAdapter):
    """
    Handle EXIT_ON_CLOSE for jdk < 1.4
    Thanks to James Richards for this method
    """
    def windowClosed(self, evt):
        import java.lang.System as System
        System.exit(0)

if __name__ == "__main__":
    frame = JythonFrame()
    console = Console()
    frame.getContentPane().add(JScrollPane(console.text_pane))
    frame.show()
