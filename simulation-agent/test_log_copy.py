"""Mutation isolation, aliasing, cycles and custom hooks match original deepcopy."""
from copy import deepcopy
import unittest
from abides_core.agent import _copy_log_event

class CustomInt(int):
    pass

class CustomDict(dict):
    def __deepcopy__(self,memo):
        return {'custom':True}

class LogCopyTests(unittest.TestCase):
    def test_atomic_values(self):
        for value in [None,True,1,10**30,1.2,float('nan'),complex(1,2),'text',b'bytes']:
            self.assertIs(_copy_log_event(value),deepcopy(value))

    def test_flat_dictionary_new_container(self):
        value={'x':1,'y':None,'z':b'bytes'}
        actual=_copy_log_event(value)
        self.assertEqual(actual,deepcopy(value));self.assertIsNot(actual,value)
        value['x']=2; self.assertEqual(actual['x'],1)

    def test_nested_alias_isolation(self):
        child=[{'quantity':10}];value={'a':child,'b':child}
        actual=_copy_log_event(value)
        self.assertEqual(actual,deepcopy(value));self.assertIs(actual['a'],actual['b'])
        child[0]['quantity']=20;self.assertEqual(actual['a'][0]['quantity'],10)

    def test_cycles(self):
        value={};value['self']=value
        actual=_copy_log_event(value);self.assertIs(actual['self'],actual);self.assertIsNot(actual,value)

    def test_custom_subclasses_preserve_hooks_and_mutability(self):
        self.assertEqual(_copy_log_event(CustomDict(x=1)),{'custom':True})
        child=CustomInt(1);child.mutable=[];value={'child':child}
        actual=_copy_log_event(value);child.mutable.append(2)
        self.assertEqual(actual['child'].mutable,[])

    def test_tuples_with_shared_mutable_members(self):
        child=[];value=(child,child)
        actual=_copy_log_event(value);self.assertIs(actual[0],actual[1]);self.assertIsNot(actual[0],child)

if __name__=='__main__':unittest.main()
