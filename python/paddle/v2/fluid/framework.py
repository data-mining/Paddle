import collections

import numpy as np
from . import core
import proto.framework_pb2 as framework_pb2
import contextlib

__all__ = [
    'Block', 'Variable', 'Program', 'Operator', 'default_startup_program',
    'default_main_program', 'program_guard', 'switch_startup_program',
    'switch_main_program'
]


def unique_name(prefix):
    uid = core.unique_integer(prefix)  # unique during whole process.
    return "_".join([prefix, str(uid)])


def convert_np_dtype_to_dtype_(np_dtype):
    dtype = np.dtype(np_dtype)
    if dtype == np.float32:
        return core.DataType.FP32
    elif dtype == np.float64:
        return core.DataType.FP64
    elif dtype == np.float16:
        return core.DataType.FP16
    elif dtype == np.int32:
        return core.DataType.INT32
    elif dtype == np.int16:
        return core.DataType.INT16
    elif dtype == np.int64:
        return core.DataType.INT64
    elif dtype == np.bool:
        return core.DataType.BOOL
    else:
        raise ValueError("Not supported numpy dtype " + str(dtype))


def dtype_is_floating(dtype):
    if not isinstance(dtype, core.DataType):
        dtype = convert_np_dtype_to_dtype_(dtype)

    if (dtype == core.DataType.FP16 or dtype == core.DataType.FP32 or
            dtype == core.DataType.FP64):
        return True
    else:
        return False


def _debug_string_(proto, throw_on_error=True):
    error_fields = list()
    if not proto.IsInitialized(error_fields) and throw_on_error:
        raise ValueError("{0} are not initialized\nThe message is {1}".format(
            error_fields, proto))
    return proto.__str__()


class Variable(object):
    def __init__(self,
                 block,
                 type=core.VarDesc.VarType.LOD_TENSOR,
                 name=None,
                 shape=None,
                 dtype=None,
                 lod_level=None,
                 persistable=None,
                 stop_gradient=False,
                 **kwargs):
        self.block = block

        if name is None:
            name = Variable._unique_var_name_()
        is_new_var = False
        self.desc = self.block.desc.find_var(name)

        if self.desc is None:
            self.desc = self.block.desc.var(name)
            is_new_var = True

        if is_new_var:
            self.desc.set_type(type)
        elif self.desc.type() != type:
            raise ValueError("Variable {0} has been created before. The "
                             "previous type is {1}; the new type is {2}. They"
                             " are not matched".format(self.name,
                                                       self.desc.type(), type))

        if shape is not None:
            if is_new_var:
                self.desc.set_shape(shape)
            else:
                old_shape = self.shape
                shape = tuple(shape)
                if shape != old_shape:
                    raise ValueError(
                        "Variable {0} has been created before. the previous "
                        "shape is {1}; the new shape is {2}. They are not "
                        "matched.".format(self.name, old_shape, shape))
        if dtype is not None:
            if not isinstance(dtype, core.DataType):
                dtype = convert_np_dtype_to_dtype_(dtype)
            if is_new_var:
                self.desc.set_dtype(dtype)
            else:
                old_dtype = self.dtype
                if dtype != old_dtype:
                    raise ValueError("Variable {0} has been created before. "
                                     "The previous data type is {1}; the new "
                                     "data type is {2}. They are not "
                                     "matched.".format(self.name, old_dtype,
                                                       dtype))

        if lod_level is not None:
            if is_new_var:
                self.desc.set_lod_level(lod_level)
            else:
                if lod_level != self.lod_level:
                    raise ValueError("Variable {0} has been created before. "
                                     "The previous lod_level is {1}; the new "
                                     "lod_level is {2}. They are not "
                                     "matched".format(self.name, self.lod_level,
                                                      lod_level))
        if persistable is not None:
            if is_new_var:
                self.desc.set_persistable(persistable)
            else:
                if persistable != self.persistable:
                    raise ValueError(
                        "Variable {0} has been created before."
                        "The previous persistable is {1}; the new "
                        "persistable is {2}. They are not matched".format(
                            self.name, self.persistable, persistable))

        self.block.vars[name] = self
        self.op = None
        self.stop_gradient = stop_gradient

    def __str__(self):
        return self.to_string(True)

    def to_string(self, throw_on_error):
        protostr = self.desc.serialize_to_string()
        proto = framework_pb2.VarDesc.FromString(str(protostr))
        return _debug_string_(proto, throw_on_error)

    __repr__ = __str__

    @property
    def persistable(self):
        return self.desc.persistable()

    @persistable.setter
    def persistable(self, p):
        self.desc.set_persistable(p)

    @property
    def name(self):
        return self.desc.name()

    @property
    def shape(self):
        # convert to tuple, make it as same as numpy API.
        return tuple(self.desc.shape())

    @property
    def dtype(self):
        return self.desc.dtype()

    @property
    def lod_level(self):
        return self.desc.lod_level()

    @property
    def type(self):
        return self.desc.type()

    @staticmethod
    def _unique_var_name_():
        prefix = "_generated_var"
        uid = core.unique_integer(prefix)  # unique during whole process.
        return "_".join([prefix, str(uid)])


def get_all_op_protos():
    """
    Get all registered op proto from PaddlePaddle C++ end.
    :return: A list of registered OpProto.
    """
    protostrs = core.get_all_op_protos()
    ret_values = []
    for pbstr in protostrs:
        op_proto = framework_pb2.OpProto.FromString(str(pbstr))
        ret_values.append(op_proto)
    return ret_values


class OpProtoHolder(object):
    @classmethod
    def instance(cls):
        if not hasattr(cls, '_instance'):
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        assert not hasattr(
            self.__class__,
            '_instance'), 'Please use `instance()` to get OpProtoHolder opject!'
        op_protos = get_all_op_protos()
        self.op_proto_map = {}
        for proto in op_protos:
            self.op_proto_map[proto.type] = proto

    def get_op_proto(self, type):
        if type not in self.op_proto_map:
            raise ValueError("Operator \"%s\" has not been registered." % type)
        return self.op_proto_map[type]


class Operator(object):
    def __init__(self,
                 block,
                 desc,
                 type=None,
                 inputs=None,
                 outputs=None,
                 attrs=None):
        self.block = block
        self.desc = desc
        if len(self.desc.type()) != 0:
            return
        if type is None:
            raise ValueError(
                "`type` to initilized an Operator can not be None.")
        self.desc.set_type(type)
        proto = OpProtoHolder.instance().get_op_proto(type)

        def find_name(var_list, name):
            for var_name in var_list:
                if var_name == name:
                    return True
            return False

        if inputs is not None:
            for in_proto in proto.inputs:
                found = find_name(inputs, in_proto.name)
                assert found or in_proto.dispensable, "Input {} not found".format(
                    in_proto.name)

                if found:
                    in_args = inputs[in_proto.name]
                    if not isinstance(in_args, list):
                        in_args = [in_args]
                    if not in_proto.duplicable and len(in_args) > 1:
                        raise ValueError(
                            "Input %s expects only one input, but %d are given."
                            % (in_proto.name, len(in_args)))
                    in_arg_names = []
                    for arg in in_args:
                        in_arg_names.append(arg.name)
                    self.desc.set_input(in_proto.name, in_arg_names)
                else:
                    self.desc.set_input(in_proto.name, [])

        if outputs is not None:
            given = set()
            need = set()
            for n in outputs:
                given.add(n)
            for m in proto.outputs:
                need.add(m.name)
            if not given == need:
                raise ValueError(
                    "Incorrect setting for output(s) of operator \"%s\". Need: [%s] Given: [%s]"
                    % (type, ", ".join(str(e) for e in need), ", ".join(
                        str(e) for e in given)))

            for out_proto in proto.outputs:
                out_args = outputs[out_proto.name]
                if not isinstance(out_args, list):
                    out_args = [out_args]
                if not out_proto.duplicable and len(out_args) > 1:
                    raise ValueError(
                        "Output %s expects only one output, but %d are given." %
                        (out_proto.name, len(out_args)))
                out_arg_names = []
                for arg in out_args:
                    out_arg_names.append(arg.name)
                    arg.op = self
                self.desc.set_output(out_proto.name, out_arg_names)

        if attrs is not None:
            if not isinstance(attrs, dict):
                raise TypeError("'attrs' should be a dict.")
            for attr in proto.attrs:
                attr_name = attr.name
                if (not attr_name in attrs) or (attrs[attr_name] is None):
                    continue
                if isinstance(attrs[attr_name], Block):
                    self.desc.set_block_attr(attr_name, attrs[attr_name].desc)
                else:
                    self.desc.set_attr(attr_name, attrs[attr_name])

        self.desc.check_attrs()
        no_kernel_op_set = {
            'feed', 'fetch', 'save', 'load', 'recurrent',
            'rnn_memory_helper_grad', 'conditional_block', 'while'
        }
        if type not in no_kernel_op_set:
            self.desc.infer_var_type(self.block.desc)
            self.desc.infer_shape(self.block.desc)

    def to_string(self, throw_on_error):
        protostr = self.desc.serialize_to_string()
        proto = framework_pb2.OpDesc.FromString(str(protostr))
        return _debug_string_(proto, throw_on_error)

    def __str__(self):
        return self.to_string(True)

    __repr__ = __str__

    @property
    def type(self):
        return self.desc.type()

    def input(self, name):
        return self.desc.input(name)

    @property
    def input_names(self):
        return self.desc.input_names()

    def output(self, name):
        return self.desc.output(name)

    @property
    def output_names(self):
        return self.desc.output_names()

    @property
    def idx(self):
        for i, op in enumerate(self.block.ops):
            if op == self:
                return i
        raise ValueError(
            "Can't find op itself in it's block. It could be a bug of Paddle.")

    def has_attr(self, name):
        return self.desc.has_attr(name)

    def attr_type(self, name):
        return self.desc.attr_type(name)

    @property
    def attr_names(self):
        return self.desc.attr_names()

    def attr(self, name):
        return self.desc.attr(name)

    def block_attr(self, name):
        return self.desc.block_attr(name)


class Block(object):
    def __init__(self, program, idx):
        self.desc = program.desc.block(idx)
        self.vars = dict()  # var_name --> var
        self.ops = collections.deque()  # operator list
        self.program = program

    def __str__(self):
        return self.to_string(True)

    def to_string(self, throw_on_error):
        protostr = self.desc.serialize_to_string()
        proto = framework_pb2.BlockDesc.FromString(str(protostr))
        return _debug_string_(proto, throw_on_error)

    __repr__ = __str__

    @property
    def parent_idx(self):
        return self.desc.parent

    @property
    def idx(self):
        return self.desc.id

    def var(self, name):
        if not isinstance(name, basestring):
            raise TypeError()
        v = self.vars.get(name, None)
        if v is None:
            raise ValueError("var %s not in this block" % name)
        return v

    def all_parameters(self):
        return list(self.iter_parameters())

    def iter_parameters(self):
        return (item[1] for item in self.vars.iteritems()
                if isinstance(item[1], Parameter))

    def create_var(self, *args, **kwargs):
        var = Variable(self, *args, **kwargs)
        if 'initializer' in kwargs:
            kwargs['initializer'](var, self)
        return var

    def has_var(self, name):
        return name in self.vars

    def create_parameter(self, *args, **kwargs):
        global_block = self.program.global_block()
        param = Parameter(global_block, *args, **kwargs)
        if 'initializer' in kwargs:
            kwargs['initializer'](param, self)
        return param

    def append_op(self, *args, **kwargs):
        op_desc = self.desc.append_op()
        op = Operator(self, op_desc, *args, **kwargs)
        self.ops.append(op)
        return op

    def prepend_op(self, *args, **kwargs):
        op_desc = self.desc.prepend_op()
        op = Operator(self, op_desc, *args, **kwargs)
        self.ops.appendleft(op)
        return op

    def sync_with_cpp(self):
        # sync variables from cpp
        for var in self.desc.all_vars():
            if not self.has_var(var.name()):
                self.create_var(name=var.name(), desc=var, type=var.type())

        # sync operators from cpp
        ops_in_cpp = []
        for op_idx in range(0, self.desc.op_size()):
            ops_in_cpp.append(self.desc.op(op_idx))

        if len(self.ops) != 0:
            first_op_in_python = self.ops[0].desc
            last_op_in_python = self.ops[len(self.ops) - 1].desc
            start_index = None
            end_index = None
            for index in range(len(ops_in_cpp)):
                if first_op_in_python == ops_in_cpp[index]:
                    start_index = index
                if last_op_in_python == ops_in_cpp[index]:
                    end_index = index
            assert start_index is not None
            assert end_index is not None
            assert start_index <= end_index
        else:
            start_index = 0
            end_index = -1

        # sync ops append to the head of cpp_ops
        for index in range((start_index - 1 - 1), -1, -1):
            op_desc = ops_in_cpp[index]
            op = Operator(self, op_desc)
            self.ops.appendleft(op)

        # sync ops append to the end of cpp_ops
        for index in range((end_index + 1), len(ops_in_cpp)):
            op_desc = ops_in_cpp[index]
            op = Operator(self, op_desc)
            self.ops.append(op)

        assert len(self.ops) == len(ops_in_cpp)
        for index in range(len(self.ops)):
            assert self.ops[index].desc == ops_in_cpp[index]

    def copy_param_info_from(self, other):
        """
        Copy the information of parameters from other block
        Args:
            other(Block): other block 

        Returns:
            None
        """
        if not isinstance(other, Block):
            raise TypeError("copy_param_info_from should be invoked with Block")
        for p in other.iter_parameters():
            assert isinstance(p, Parameter)
            v = self.vars.get(p.name, None)
            if v is None:
                raise ValueError("copy_param_info_from should be invoked with "
                                 "same topology")
            assert isinstance(v, Variable)
            new_p = Parameter(
                block=self,
                shape=v.shape,
                dtype=v.dtype,
                type=v.type,
                lod_level=v.lod_level,
                stop_gradient=p.stop_gradient,
                trainable=p.trainable,
                optimize_attr=p.optimize_attr,
                regularizer=p.regularizer,
                name=v.name)
            self.vars[new_p.name] = new_p


class Program(object):
    def __init__(self):
        self.desc = core.ProgramDesc()
        self.blocks = [Block(self, 0)]
        self.current_block_idx = 0

    def __str__(self):
        return self.to_string(True)

    def to_string(self, throw_on_error):
        protostr = self.desc.serialize_to_string()
        proto = framework_pb2.ProgramDesc.FromString(str(protostr))
        return _debug_string_(proto, throw_on_error)

    def clone(self):
        p = Program()
        p.desc = core.ProgramDesc(self.desc)
        p.blocks = [Block(p, i) for i in xrange(self.desc.num_blocks())]
        p.sync_with_cpp()
        p.copy_param_info_from(self)
        return p

    def prune(self, targets):
        if not isinstance(targets, list):
            targets = [targets]
        targets_idx = []
        for t in targets:
            if not isinstance(t, Operator):
                if isinstance(t, Variable):
                    t = t.op
                else:
                    raise ValueError(
                        "All targets of prune() can only be Variable or Operator."
                    )

            targets_idx.append([t.block.idx, t.idx])
        res = Program()
        res.desc = core.prune(self.desc, targets_idx)
        res.blocks = [Block(res, i) for i in xrange(res.desc.num_blocks())]
        res.sync_with_cpp()
        return res

    def inference_optimize(self):
        res = Program()
        res.desc = core.inference_optimize(self.desc)
        res.blocks = [Block(res, i) for i in xrange(res.desc.num_blocks())]
        res.sync_with_cpp()
        return res

    @staticmethod
    def parse_from_string(binary_str):
        p = Program()
        p.desc = core.ProgramDesc(binary_str)
        p.blocks = [Block(p, i) for i in xrange(p.desc.num_blocks())]
        p.sync_with_cpp()
        return p

    def __repr__(self):
        return str(self)

    def global_block(self):
        return self.blocks[0]

    def block(self, index):
        return self.blocks[index]

    def current_block(self):
        return self.blocks[self.current_block_idx]

    def append_backward(self, target, no_grad_set=None):
        """
        return map(param_name -> (grad_name, block_index, op_index))
        """
        assert isinstance(target, Variable)
        if no_grad_set is None:
            no_grad_set = set()
        try:
            param_to_grad_info = self.desc.append_backward(target.desc,
                                                           no_grad_set)
        except Exception as e:
            raise core.EnforceNotMet(
                str(e) + "\nCurrent protobuf is\n{0}".format(
                    self.to_string(False)))

        self.sync_with_cpp()
        return param_to_grad_info

    def create_block(self):
        new_block_idx = len(self.blocks)
        self.desc.append_block(self.current_block().desc)
        self.current_block_idx = new_block_idx
        self.blocks.append(Block(self, self.current_block_idx))
        return self.current_block()

    def rollback(self):
        self.current_block_idx = self.current_block().parent_idx

    def sync_with_cpp(self):
        for block_idx in range(len(self.blocks), self.desc.num_blocks()):
            self.blocks.append(Block(self, block_idx))
        for block in self.blocks:
            block.sync_with_cpp()

    def copy_param_info_from(self, other):
        """
        Copy the information of parameters from other program. 
        Args:
            other(Program): Other program

        Returns:
            None
        """
        if not isinstance(other, Program):
            raise TypeError("copy_param_info_from should be invoked with "
                            "Program")

        if len(self.blocks) != len(other.blocks):
            raise ValueError("copy_param_info_from should be invoked with two "
                             "program, with represent the same topology")
        self.global_block().copy_param_info_from(other.global_block())

    def list_vars(self):
        for each_block in self.blocks:
            for each_var in each_block.vars.itervalues():
                yield each_var


class Parameter(Variable):
    def __init__(self, block, shape, dtype, **kwargs):
        if shape is None or dtype is None:
            raise ValueError("Parameter must set shape and dtype")
        if len(shape) == 0:
            raise ValueError("Parameter shape cannot be empty")

        for each in shape:
            if each < 0:
                raise ValueError("Parameter shape should not be related with "
                                 "batch-size")

        Variable.__init__(
            self, block, persistable=True, shape=shape, dtype=dtype, **kwargs)
        self.trainable = kwargs.get('trainable', True)

        self.optimize_attr = kwargs.get('optimize_attr', {'learning_rate': 1.0})

        self.regularizer = kwargs.get('regularizer', None)


# program is a global instance.
_main_program_ = Program()
_startup_program_ = Program()


def default_startup_program():
    """
    Get default startup program. In startup program, Paddle will initialize
    parameters, initialize nccl handle, etc.
    
    Returns:
        Program: startup program
    """
    return _startup_program_


def default_main_program():
    """
    Get default main program. The main program is used for training or testing.
    
    Returns:
        Program: main program
    """
    return _main_program_


def switch_main_program(program):
    """
    Switch the main program to a new program.
    
    Args:
        program(Program): The new main program

    Returns:
        Program: The previous main program
    """
    global _main_program_
    prev_program = _main_program_
    _main_program_ = program
    return prev_program


def switch_startup_program(program):
    """
    Switch the startup program to a new program 
    Args:
        program(Program): The new startup program

    Returns:
        Program: The previous startup program
    """
    global _startup_program_
    prev_program = _startup_program_
    _startup_program_ = program
    return prev_program


@contextlib.contextmanager
def program_guard(main_program, startup_program=None):
    """
    Switch program with `with` statement
    
    Examples:
        >>> with program_guard(Program()):
        >>>   data = fluid.layers.data(...)
        >>>   hidden = fluid.layers.fc(...)
        
    Args:
        main_program(Program): New main program inside `with` statement
        startup_program(Program): New startup program inside `with` statement. 
            None means do not change startup program.

    Returns:
        None
    """
    if not isinstance(main_program, Program):
        raise TypeError("main_program should be Program")
    main_program = switch_main_program(main_program)
    if startup_program is not None:
        if not isinstance(startup_program, Program):
            raise TypeError("startup_program should be Program")
        startup_program = switch_startup_program(startup_program)
    yield
    switch_main_program(main_program)
    if startup_program is not None:
        switch_startup_program(startup_program)
