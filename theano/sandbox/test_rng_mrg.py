import os
import sys
import time

import numpy
import theano
from theano import tensor, config
from theano.sandbox import rng_mrg
from theano.sandbox.rng_mrg import MRG_RandomStreams
from theano.sandbox.cuda import cuda_available
from theano.gof.python25 import any

if cuda_available:
    from theano.sandbox.cuda import float32_shared_constructor

import unittest
from theano.tests import unittest_tools as utt
from nose.plugins.skip import SkipTest
from nose.plugins.attrib import attr

#TODO: test gpu
# Done in test_consistency_GPU_{serial,parallel}

#TODO: test MRG_RandomStreams
# Partly done in test_consistency_randomstreams

#TODO: test optimizer mrg_random_make_inplace
#TODO: make tests work when no flags gived. Now need:
#      THEANO_FLAGS=device=gpu0,floatX=float32
# Partly done, in test_consistency_GPU_{serial,parallel}


mode = config.mode
mode_with_gpu = theano.compile.mode.get_default_mode().including('gpu')
utt.seed_rng()

## Results generated by Java code using L'Ecuyer et al.'s code, with:
# main seed: [12345]*6 (default)
# 12 streams
# 7 substreams for each stream
# 5 samples drawn from each substream
java_samples = numpy.loadtxt(os.path.join(os.path.split(theano.__file__)[0],
                                          'sandbox',
                                          'samples_MRG31k3p_12_7_5.txt'))


def test_deterministic():
    seed = utt.fetch_seed()
    sample_size = (10, 20)

    test_use_cuda = [False]
    if cuda_available:
        test_use_cuda.append(True)

    for use_cuda in test_use_cuda:
        #print 'use_cuda =', use_cuda
        R = MRG_RandomStreams(seed=seed, use_cuda=use_cuda)
        u = R.uniform(size=sample_size)
        f = theano.function([], u)

        fsample1 = f()
        fsample2 = f()
        assert not numpy.allclose(fsample1, fsample2)

        R2 = MRG_RandomStreams(seed=seed, use_cuda=use_cuda)
        u2 = R2.uniform(size=sample_size)
        g = theano.function([], u2)
        gsample1 = g()
        gsample2 = g()
        assert numpy.allclose(fsample1, gsample1)
        assert numpy.allclose(fsample2, gsample2)


def test_consistency_randomstreams():
    '''Verify that the random numbers generated by MRG_RandomStreams
    are the same as the reference (Java) implementation by L'Ecuyer et al.
    '''

    seed = 12345
    n_samples = 5
    n_streams = 12
    n_substreams = 7

    test_use_cuda = [False]
    if cuda_available:
        test_use_cuda.append(True)

    for use_cuda in test_use_cuda:
        #print 'use_cuda =', use_cuda
        samples = []
        rng = MRG_RandomStreams(seed=seed, use_cuda=False)
        for i in range(n_streams):
            stream_samples = []
            u = rng.uniform(size=(n_substreams,), nstreams=n_substreams)
            f = theano.function([], u)
            for j in range(n_samples):
                s = f()
                stream_samples.append(s)
            stream_samples = numpy.array(stream_samples)
            stream_samples = stream_samples.T.flatten()
            samples.append(stream_samples)

        samples = numpy.array(samples).flatten()
        assert(numpy.allclose(samples, java_samples))


def test_consistency_cpu_serial():
    '''Verify that the random numbers generated by mrg_uniform, serially,
    are the same as the reference (Java) implementation by L'Ecuyer et al.
    '''
    seed = 12345
    n_samples = 5
    n_streams = 12
    n_substreams = 7

    samples = []
    curr_rstate = numpy.array([seed] * 6, dtype='int32')

    for i in range(n_streams):
        stream_rstate = curr_rstate.copy()
        for j in range(n_substreams):
            rstate = theano.shared(numpy.array([stream_rstate.copy()],
                                               dtype='int32'))
            new_rstate, sample = rng_mrg.mrg_uniform.new(rstate, ndim=None,
                                                         dtype=config.floatX,
                                                         size=(1,))
            # Not really necessary, just mimicking
            # rng_mrg.MRG_RandomStreams' behavior
            sample.rstate = rstate
            sample.update = (rstate, new_rstate)

            rstate.default_update = new_rstate
            f = theano.function([], sample)
            for k in range(n_samples):
                s = f()
                samples.append(s)

            # next substream
            stream_rstate = rng_mrg.ff_2p72(stream_rstate)

        # next stream
        curr_rstate = rng_mrg.ff_2p134(curr_rstate)

    samples = numpy.array(samples).flatten()
    assert(numpy.allclose(samples, java_samples))


def test_consistency_cpu_parallel():
    '''Verify that the random numbers generated by mrg_uniform, in parallel,
    are the same as the reference (Java) implementation by L'Ecuyer et al.
    '''
    seed = 12345
    n_samples = 5
    n_streams = 12
    n_substreams = 7  # 7 samples will be drawn in parallel

    samples = []
    curr_rstate = numpy.array([seed] * 6, dtype='int32')

    for i in range(n_streams):
        stream_samples = []
        rstate = [curr_rstate.copy()]
        for j in range(1, n_substreams):
            rstate.append(rng_mrg.ff_2p72(rstate[-1]))
        rstate = numpy.asarray(rstate)
        rstate = theano.shared(rstate)

        new_rstate, sample = rng_mrg.mrg_uniform.new(rstate, ndim=None,
                dtype=config.floatX, size=(n_substreams,))
        # Not really necessary, just mimicking
        # rng_mrg.MRG_RandomStreams' behavior
        sample.rstate = rstate
        sample.update = (rstate, new_rstate)

        rstate.default_update = new_rstate
        f = theano.function([], sample)

        for k in range(n_samples):
            s = f()
            stream_samples.append(s)

        samples.append(numpy.array(stream_samples).T.flatten())

        # next stream
        curr_rstate = rng_mrg.ff_2p134(curr_rstate)

    samples = numpy.array(samples).flatten()
    assert(numpy.allclose(samples, java_samples))


def test_consistency_GPU_serial():
    '''Verify that the random numbers generated by GPU_mrg_uniform, serially,
    are the same as the reference (Java) implementation by L'Ecuyer et al.
    '''
    if not cuda_available:
        raise SkipTest('Optional package cuda not available')
    if config.mode == 'FAST_COMPILE':
        mode = 'FAST_RUN'
    else:
        mode = config.mode

    seed = 12345
    n_samples = 5
    n_streams = 12
    n_substreams = 7

    samples = []
    curr_rstate = numpy.array([seed] * 6, dtype='int32')

    for i in range(n_streams):
        stream_rstate = curr_rstate.copy()
        for j in range(n_substreams):
            substream_rstate = numpy.array(stream_rstate.copy(), dtype='int32')
            # HACK - we transfer these int32 to the GPU memory as float32
            # (reinterpret_cast)
            tmp_float_buf = numpy.frombuffer(substream_rstate.data,
                                             dtype='float32')
            # Transfer to device
            rstate = float32_shared_constructor(tmp_float_buf)

            new_rstate, sample = rng_mrg.GPU_mrg_uniform.new(rstate, ndim=None,
                    dtype='float32', size=(1,))
            rstate.default_update = new_rstate

            # Not really necessary, just mimicking
            # rng_mrg.MRG_RandomStreams' behavior
            sample.rstate = rstate
            sample.update = (rstate, new_rstate)

            # We need the sample back in the main memory
            cpu_sample = tensor.as_tensor_variable(sample)
            f = theano.function([], cpu_sample, mode=mode)
            for k in range(n_samples):
                s = f()
                samples.append(s)

            # next substream
            stream_rstate = rng_mrg.ff_2p72(stream_rstate)

        # next stream
        curr_rstate = rng_mrg.ff_2p134(curr_rstate)

    samples = numpy.array(samples).flatten()
    assert(numpy.allclose(samples, java_samples))


def test_consistency_GPU_parallel():
    '''Verify that the random numbers generated by GPU_mrg_uniform, in
    parallel, are the same as the reference (Java) implementation by
    L'Ecuyer et al.

    '''
    if not cuda_available:
        raise SkipTest('Optional package cuda not available')
    if config.mode == 'FAST_COMPILE':
        mode = 'FAST_RUN'
    else:
        mode = config.mode

    seed = 12345
    n_samples = 5
    n_streams = 12
    n_substreams = 7  # 7 samples will be drawn in parallel

    samples = []
    curr_rstate = numpy.array([seed] * 6, dtype='int32')

    for i in range(n_streams):
        stream_samples = []
        rstate = [curr_rstate.copy()]
        for j in range(1, n_substreams):
            rstate.append(rng_mrg.ff_2p72(rstate[-1]))
        rstate = numpy.asarray(rstate).flatten()
        # HACK - transfer these int32 to the GPU memory as float32
        # (reinterpret_cast)
        tmp_float_buf = numpy.frombuffer(rstate.data, dtype='float32')
        # Transfer to device
        rstate = float32_shared_constructor(tmp_float_buf)

        new_rstate, sample = rng_mrg.GPU_mrg_uniform.new(rstate, ndim=None,
                dtype='float32', size=(n_substreams,))
        rstate.default_update = new_rstate

        # Not really necessary, just mimicking
        # rng_mrg.MRG_RandomStreams' behavior
        sample.rstate = rstate
        sample.update = (rstate, new_rstate)

        # We need the sample back in the main memory
        cpu_sample = tensor.as_tensor_variable(sample)
        f = theano.function([], cpu_sample, mode=mode)

        for k in range(n_samples):
            s = f()
            stream_samples.append(s)

        samples.append(numpy.array(stream_samples).T.flatten())

        # next stream
        curr_rstate = rng_mrg.ff_2p134(curr_rstate)

    samples = numpy.array(samples).flatten()
    assert(numpy.allclose(samples, java_samples))


def test_consistency_GPUA_serial():
    '''Verify that the random numbers generated by GPUA_mrg_uniform, serially,
    are the same as the reference (Java) implementation by L'Ecuyer et al.
    '''
    from theano.sandbox.gpuarray.tests.test_basic_ops import \
        mode_with_gpu as mode
    from theano.sandbox.gpuarray.type import gpuarray_shared_constructor

    seed = 12345
    n_samples = 5
    n_streams = 12
    n_substreams = 7

    samples = []
    curr_rstate = numpy.array([seed] * 6, dtype='int32')

    for i in range(n_streams):
        stream_rstate = curr_rstate.copy()
        for j in range(n_substreams):
            substream_rstate = numpy.array(stream_rstate.copy(), dtype='int32')
            # Transfer to device
            rstate = gpuarray_shared_constructor(substream_rstate)

            new_rstate, sample = rng_mrg.GPUA_mrg_uniform.new(rstate,
                                                              ndim=None,
                                                              dtype='float32',
                                                              size=(1,))
            rstate.default_update = new_rstate

            # Not really necessary, just mimicking
            # rng_mrg.MRG_RandomStreams' behavior
            sample.rstate = rstate
            sample.update = (rstate, new_rstate)

            # We need the sample back in the main memory
            cpu_sample = tensor.as_tensor_variable(sample)
            f = theano.function([], cpu_sample, mode=mode)
            for k in range(n_samples):
                s = f()
                samples.append(s)

            # next substream
            stream_rstate = rng_mrg.ff_2p72(stream_rstate)

        # next stream
        curr_rstate = rng_mrg.ff_2p134(curr_rstate)

    samples = numpy.array(samples).flatten()
    assert(numpy.allclose(samples, java_samples))


def test_consistency_GPUA_parallel():
    '''Verify that the random numbers generated by GPUA_mrg_uniform, in
    parallel, are the same as the reference (Java) implementation by
    L'Ecuyer et al.

    '''
    from theano.sandbox.gpuarray.tests.test_basic_ops import \
        mode_with_gpu as mode
    from theano.sandbox.gpuarray.type import gpuarray_shared_constructor

    seed = 12345
    n_samples = 5
    n_streams = 12
    n_substreams = 7  # 7 samples will be drawn in parallel

    samples = []
    curr_rstate = numpy.array([seed] * 6, dtype='int32')

    for i in range(n_streams):
        stream_samples = []
        rstate = [curr_rstate.copy()]
        for j in range(1, n_substreams):
            rstate.append(rng_mrg.ff_2p72(rstate[-1]))
        rstate = numpy.asarray(rstate).flatten()
        rstate = gpuarray_shared_constructor(rstate)

        new_rstate, sample = rng_mrg.GPUA_mrg_uniform.new(rstate, ndim=None,
                dtype='float32', size=(n_substreams,))
        rstate.default_update = new_rstate

        # Not really necessary, just mimicking
        # rng_mrg.MRG_RandomStreams' behavior
        sample.rstate = rstate
        sample.update = (rstate, new_rstate)

        # We need the sample back in the main memory
        cpu_sample = tensor.as_tensor_variable(sample)
        f = theano.function([], cpu_sample, mode=mode)

        for k in range(n_samples):
            s = f()
            stream_samples.append(s)

        samples.append(numpy.array(stream_samples).T.flatten())

        # next stream
        curr_rstate = rng_mrg.ff_2p134(curr_rstate)

    samples = numpy.array(samples).flatten()
    assert(numpy.allclose(samples, java_samples))


def basictest(f, steps, sample_size, prefix="", allow_01=False, inputs=None,
              target_avg=0.5, target_std=None, mean_rtol=0.01, std_tol=0.01):
    if inputs is None:
        inputs = []
    dt = 0.0
    avg_var = 0.0

    for i in xrange(steps):
        t0 = time.time()
        ival = f(*inputs)
        assert ival.shape == sample_size
        dt += time.time() - t0
        ival = numpy.asarray(ival)
        if i == 0:
            mean = numpy.array(ival, copy=True)
            avg_var = numpy.mean((ival - target_avg) ** 2)
            min_ = ival.min()
            max_ = ival.max()
        else:
            alpha = 1.0 / (1 + i)
            mean = alpha * ival + (1 - alpha) * mean
            avg_var = (alpha * numpy.mean((ival - target_avg) ** 2)
                       + (1 - alpha) * avg_var)
            min_ = min(min_, ival.min())
            max_ = max(max_, ival.max())
        if not allow_01:
            assert min_ > 0
            assert max_ < 1

    if hasattr(target_avg, 'shape'):  # looks if target_avg is an array
        diff = numpy.mean(abs(mean - target_avg))
        #print prefix, 'mean diff with mean', diff
        assert numpy.all(diff < mean_rtol * (1 + abs(target_avg))), (
            'bad mean? %s %s' % (mean, target_avg))
    else:  # if target_avg is a scalar, then we can do the mean of
           # `mean` to get something more precise
        mean = numpy.mean(mean)
        #print prefix, 'mean', mean
        assert abs(mean - target_avg) < mean_rtol * (1 + abs(target_avg)), (
            'bad mean? %f %f' % (mean, target_avg))

    std = numpy.sqrt(avg_var)
    #print prefix, 'var', avg_var
    #print prefix, 'std', std
    if target_std is not None:
        assert abs(std - target_std) < std_tol * (1 + abs(target_std)), (
                'bad std? %f %f %f' % (std, target_std, std_tol))
    #print prefix, 'time', dt
    #print prefix, 'elements', steps * sample_size[0] * sample_size[1]
    #print prefix, 'samples/sec', steps * sample_size[0] * sample_size[1] / dt
    #print prefix, 'min', min_, 'max', max_


def test_uniform():
#TODO: test param low, high
#TODO: test size=None
#TODO: test ndim!=size.ndim
#TODO: test bad seed
#TODO: test size=Var, with shape that change from call to call
    if (mode in ['DEBUG_MODE', 'DebugMode', 'FAST_COMPILE'] or
        mode == 'Mode' and config.linker in ['py']):
        sample_size = (10, 100)
        steps = 50
    else:
        sample_size = (500, 50)
        steps = int(1e3)

    x = tensor.matrix()
    for size, const_size, var_input, input in [
            (sample_size, sample_size, [], []),
            (x.shape, sample_size, [x],
             [numpy.zeros(sample_size, dtype=config.floatX)]),
            ((x.shape[0], sample_size[1]), sample_size, [x],
             [numpy.zeros(sample_size, dtype=config.floatX)]),
            # test empty size (scalar)
            ((), (), [], []),
            ]:

        #### TEST CPU IMPLEMENTATION ####
        # The python and C implementation are tested with DebugMode
        #print ''
        #print 'ON CPU with size=(%s):' % str(size)
        x = tensor.matrix()
        R = MRG_RandomStreams(234, use_cuda=False)
        # Note: we specify `nstreams` to avoid a warning.
        # TODO Look for all occurrences of `guess_n_streams` and `30 * 256`
        # for such situations: it would be better to instead filter the
        # warning using the warning module.
        u = R.uniform(size=size,
                      nstreams=rng_mrg.guess_n_streams(size, warn=False))
        f = theano.function(var_input, u, mode=mode)
        assert any([isinstance(node.op, theano.sandbox.rng_mrg.mrg_uniform)
                    for node in f.maker.fgraph.toposort()])
        #theano.printing.debugprint(f)
        cpu_out = f(*input)

        #print 'CPU: random?[:10], random?[-10:]'
        #print cpu_out[0, 0:10]
        #print cpu_out[-1, -10:]

        # Increase the number of steps if sizes implies only a few samples
        if numpy.prod(const_size) < 10:
            steps_ = steps * 100
        else:
            steps_ = steps
        basictest(f, steps_, const_size, prefix='mrg cpu', inputs=input)

        if mode != 'FAST_COMPILE' and cuda_available:
            #print ''
            #print 'ON GPU with size=(%s):' % str(size)
            R = MRG_RandomStreams(234, use_cuda=True)
            u = R.uniform(size=size, dtype='float32',
                          nstreams=rng_mrg.guess_n_streams(size, warn=False))
            # well, it's really that this test w GPU doesn't make sense otw
            assert u.dtype == 'float32'
            f = theano.function(var_input, theano.Out(
                    theano.sandbox.cuda.basic_ops.gpu_from_host(u),
                    borrow=True), mode=mode_with_gpu)
            assert any([isinstance(node.op,
                                   theano.sandbox.rng_mrg.GPU_mrg_uniform)
                        for node in f.maker.fgraph.toposort()])
            #theano.printing.debugprint(f)
            gpu_out = numpy.asarray(f(*input))

            #print 'GPU: random?[:10], random?[-10:]'
            #print gpu_out[0, 0:10]
            #print gpu_out[-1, -10:]
            basictest(f, steps_, const_size, prefix='mrg  gpu', inputs=input)

            numpy.testing.assert_array_almost_equal(cpu_out, gpu_out,
                                                    decimal=6)

        #print ''
        #print 'ON CPU w Numpy with size=(%s):' % str(size)
        RR = theano.tensor.shared_randomstreams.RandomStreams(234)

        uu = RR.uniform(size=size)
        ff = theano.function(var_input, uu, mode=mode)
        # It's not our problem if numpy generates 0 or 1
        basictest(ff, steps_, const_size, prefix='numpy',
                  allow_01=True, inputs=input)


@attr('slow')
def test_binomial():
#TODO: test size=None, ndim=X
#TODO: test size=X, ndim!=X.ndim
#TODO: test random seed in legal value(!=0 and other)
#TODO: test sample_size not a multiple of guessed #streams
#TODO: test size=Var, with shape that change from call to call
#we test size in a tuple of int and a tensor.shape.
#we test the param p with int.

    if (mode in ['DEBUG_MODE', 'DebugMode', 'FAST_COMPILE'] or
        mode == 'Mode' and config.linker in ['py']):
        sample_size = (10, 50)
        steps = 50
        rtol = 0.02
    else:
        sample_size = (500, 50)
        steps = int(1e3)
        rtol = 0.01

    x = tensor.matrix()
    v = tensor.vector()
    for mean in [0.1, 0.5]:
        for size, const_size, var_input, input in [
                (sample_size, sample_size, [], []),
                (x.shape, sample_size, [x],
                 [numpy.zeros(sample_size, dtype=config.floatX)]),
                ((x.shape[0], sample_size[1]), sample_size, [x],
                 [numpy.zeros(sample_size, dtype=config.floatX)]),
                # test empty size (scalar)
                ((), (), [], []),
                ]:

            #print ''
            #print 'ON CPU with size=(%s) and mean(%d):' % (str(size), mean)
            R = MRG_RandomStreams(234, use_cuda=False)
            # Note: we specify `nstreams` to avoid a warning.
            u = R.binomial(size=size, p=mean,
                           nstreams=rng_mrg.guess_n_streams(size, warn=False))
            f = theano.function(var_input, u, mode=mode)
            #theano.printing.debugprint(f)
            out = f(*input)
            #print 'random?[:10]\n', out[0, 0:10]
            #print 'random?[-1,-10:]\n', out[-1, -10:]

            # Increase the number of steps if sizes implies only a few samples
            if numpy.prod(const_size) < 10:
                steps_ = steps * 100
            else:
                steps_ = steps
            basictest(f, steps_, const_size, prefix='mrg  cpu',
                      inputs=input, allow_01=True,
                      target_avg=mean, mean_rtol=rtol)

            if mode != 'FAST_COMPILE' and cuda_available:
                #print ''
                #print 'ON GPU with size=(%s) and mean(%d):' % (str(size), mean)
                R = MRG_RandomStreams(234, use_cuda=True)
                u = R.binomial(size=size, p=mean, dtype='float32',
                               nstreams=rng_mrg.guess_n_streams(size,
                                                                warn=False))
                #well, it's really that this test w GPU doesn't make sense otw
                assert u.dtype == 'float32'
                f = theano.function(var_input, theano.Out(
                        theano.sandbox.cuda.basic_ops.gpu_from_host(u),
                        borrow=True), mode=mode_with_gpu)
                #theano.printing.debugprint(f)
                gpu_out = numpy.asarray(f(*input))
                #print 'random?[:10]\n', gpu_out[0, 0:10]
                #print 'random?[-1,-10:]\n', gpu_out[-1, -10:]
                basictest(f, steps_, const_size, prefix='mrg  gpu',
                          inputs=input, allow_01=True,
                          target_avg=mean, mean_rtol=rtol)
                numpy.testing.assert_array_almost_equal(out, gpu_out,
                                                        decimal=6)

            #print ''
            #print 'ON CPU w NUMPY with size=(%s) and mean(%d):' % (str(size),
            #                                                       mean)
            RR = theano.tensor.shared_randomstreams.RandomStreams(234)

            uu = RR.binomial(size=size, p=mean)
            ff = theano.function(var_input, uu, mode=mode)
            # It's not our problem if numpy generates 0 or 1
            basictest(ff, steps_, const_size, prefix='numpy', allow_01=True,
                      inputs=input, target_avg=mean, mean_rtol=rtol)


@attr('slow')
def test_normal0():

    steps = 50
    std = 2.
    if (mode in ['DEBUG_MODE', 'DebugMode', 'FAST_COMPILE'] or
        mode == 'Mode' and config.linker in ['py']):
        sample_size = (25, 30)
        default_rtol = .02
    else:
        sample_size = (999, 50)
        default_rtol = .01
    sample_size_odd = (sample_size[0], sample_size[1] - 1)
    x = tensor.matrix()

    for size, const_size, var_input, input, avg, rtol, std_tol in [
        (sample_size, sample_size, [], [], -5., default_rtol, default_rtol),
        (x.shape, sample_size, [x],
         [numpy.zeros(sample_size, dtype=config.floatX)],
         -5., default_rtol, default_rtol),
        ((x.shape[0], sample_size[1]), sample_size, [x],
         [numpy.zeros(sample_size, dtype=config.floatX)],
         -5., default_rtol, default_rtol),
        #test odd value
        (sample_size_odd, sample_size_odd, [], [], -5.,
         default_rtol, default_rtol),
        #test odd value
        (x.shape, sample_size_odd, [x],
         [numpy.zeros(sample_size_odd, dtype=config.floatX)],
         -5., default_rtol, default_rtol),
        (sample_size, sample_size, [], [],
         numpy.arange(numpy.prod(sample_size),
                      dtype='float32').reshape(sample_size),
         10. * std / numpy.sqrt(steps), default_rtol),
        # test empty size (scalar)
        ((), (), [], [], -5., default_rtol, 0.02),
        # test with few samples at the same time
        ((1,), (1,), [], [], -5., default_rtol, 0.02),
        ((2,), (2,), [], [], -5., default_rtol, 0.02),
        ((3,), (3,), [], [], -5., default_rtol, 0.02),
            ]:
        #print ''
        #print 'ON CPU:'

        R = MRG_RandomStreams(234, use_cuda=False)
        # Note: we specify `nstreams` to avoid a warning.
        n = R.normal(size=size, avg=avg, std=std,
                     nstreams=rng_mrg.guess_n_streams(size, warn=False))
        f = theano.function(var_input, n, mode=mode)
        #theano.printing.debugprint(f)
        out = f(*input)
        #print 'random?[:10]\n', out[0, 0:10]

        # Increase the number of steps if size implies only a few samples
        if numpy.prod(const_size) < 10:
            steps_ = steps * 50
        else:
            steps_ = steps
        basictest(f, steps_, const_size, target_avg=avg, target_std=std,
                  prefix='mrg ', allow_01=True, inputs=input,
                  mean_rtol=rtol, std_tol=std_tol)

        sys.stdout.flush()

        if mode != 'FAST_COMPILE' and cuda_available:
            #print ''
            #print 'ON GPU:'
            R = MRG_RandomStreams(234, use_cuda=True)
            n = R.normal(size=size, avg=avg, std=std, dtype='float32',
                         nstreams=rng_mrg.guess_n_streams(size, warn=False))
            #well, it's really that this test w GPU doesn't make sense otw
            assert n.dtype == 'float32'
            f = theano.function(var_input, theano.Out(
                theano.sandbox.cuda.basic_ops.gpu_from_host(n),
                borrow=True), mode=mode_with_gpu)

            #theano.printing.debugprint(f)
            sys.stdout.flush()
            gpu_out = numpy.asarray(f(*input))
            #print 'random?[:10]\n', gpu_out[0, 0:10]
            #print '----'
            sys.stdout.flush()
            basictest(f, steps_, const_size, target_avg=avg, target_std=std,
                      prefix='gpu mrg ', allow_01=True, inputs=input,
                      mean_rtol=rtol, std_tol=std_tol)
            # Need to allow some rounding error as their is float
            # computation that are done on the gpu vs cpu
            assert numpy.allclose(out, gpu_out, rtol=5e-6, atol=5e-6)

        #print ''
        #print 'ON CPU w NUMPY:'
        RR = theano.tensor.shared_randomstreams.RandomStreams(234)

        nn = RR.normal(size=size, avg=avg, std=std)
        ff = theano.function(var_input, nn)

        basictest(ff, steps_, const_size, target_avg=avg, target_std=std,
                  prefix='numpy ', allow_01=True, inputs=input, mean_rtol=rtol)


def basic_multinomialtest(f, steps, sample_size, target_pvals,
                          prefix="", mean_rtol=0.04):

    dt = 0.0
    avg_pvals = numpy.zeros(target_pvals.shape, dtype=config.floatX)

    for i in xrange(steps):
        t0 = time.time()
        ival = f()
        assert ival.shape == sample_size
        dt += time.time() - t0
        #ival = numpy.asarray(ival)
        avg_pvals += ival
    avg_pvals /= steps

    print 'random?[:10]\n', numpy.asarray(f()[:10])
    print prefix, 'mean', avg_pvals
    # < mean_rtol, 'bad mean? %s %s' % (str(avg_pvals), str(target_pvals))
    print numpy.mean(abs(avg_pvals - target_pvals))
    print prefix, 'time', dt
    print prefix, 'elements', steps * numpy.prod(target_pvals.shape)
    print prefix, 'samples/sec', steps * numpy.prod(target_pvals.shape) / dt


def test_multinomial():

    steps = 100
    mode_ = mode
    if mode == 'FAST_COMPILE':
        mode_ = 'FAST_RUN'

    if (mode in ['DEBUG_MODE', 'DebugMode', 'FAST_COMPILE'] or
        mode == 'Mode' and config.linker in ['py']):
        sample_size = (49, 5)
    else:
        sample_size = (450, 6)
    mode_ = theano.compile.mode.get_mode(mode_)
    #print ''
    #print 'ON CPU:'

    pvals = numpy.asarray(numpy.random.uniform(size=sample_size))
    pvals = numpy.apply_along_axis(lambda row: row / numpy.sum(row), 1, pvals)
    R = MRG_RandomStreams(234, use_cuda=False)
    # Note: we specify `nstreams` to avoid a warning.
    m = R.multinomial(pvals=pvals, dtype=config.floatX, nstreams=30 * 256)
    f = theano.function([], m, mode=mode_)
    #theano.printing.debugprint(f)
    out = f()
    basic_multinomialtest(f, steps, sample_size, pvals, prefix='mrg ')

    sys.stdout.flush()

    if mode != 'FAST_COMPILE' and cuda_available:
        #print ''
        #print 'ON GPU:'
        R = MRG_RandomStreams(234, use_cuda=True)
        pvals = numpy.asarray(pvals, dtype='float32')
        # We give the number of streams to avoid a warning.
        n = R.multinomial(pvals=pvals, dtype='float32', nstreams=30 * 256)
        #well, it's really that this test w GPU doesn't make sense otw
        assert n.dtype == 'float32'
        f = theano.function(
                [],
                theano.sandbox.cuda.basic_ops.gpu_from_host(n),
                mode=mode_.including('gpu'))

        #theano.printing.debugprint(f)
        gpu_out = f()
        sys.stdout.flush()
        basic_multinomialtest(f, steps, sample_size, pvals, prefix='gpu mrg ')
        numpy.testing.assert_array_almost_equal(out, gpu_out, decimal=6)


class T_MRG(unittest.TestCase):
    def test_bad_size(self):

        R = MRG_RandomStreams(234, use_cuda=False)

        for size in [
                (0, 100),
                (-1, 100),
                (1, 0),
                ]:

            self.assertRaises(ValueError, R.uniform, size)
            self.assertRaises(ValueError, R.binomial, size)
            self.assertRaises(ValueError, R.multinomial, size, 1, [])
            self.assertRaises(ValueError, R.normal, size)


def test_multiple_rng_aliasing():
    """
    Test that when we have multiple random number generators, we do not alias
    the state_updates member. `state_updates` can be useful when attempting to
    copy the (random) state between two similar theano graphs. The test is
    meant to detect a previous bug where state_updates was initialized as a
    class-attribute, instead of the __init__ function.
    """
    rng1 = MRG_RandomStreams(1234)
    rng2 = MRG_RandomStreams(2392)
    assert rng1.state_updates is not rng2.state_updates


def test_random_state_transfer():
    """
    Test that random state can be transferred from one theano graph to another.
    """
    class Graph:
        def __init__(self, seed=123):
            self.rng = MRG_RandomStreams(seed)
            self.y = self.rng.uniform(size=(1,))
    g1 = Graph(seed=123)
    f1 = theano.function([], g1.y)
    g2 = Graph(seed=987)
    f2 = theano.function([], g2.y)

    g2.rng.rstate = g1.rng.rstate
    for (su1, su2) in zip(g1.rng.state_updates, g2.rng.state_updates):
        su2[0].set_value(su1[0].get_value())

    numpy.testing.assert_array_almost_equal(f1(), f2(), decimal=6)


def test_gradient_scan():
    # Test for a crash when using MRG inside scan and taking the gradient
    # See https://groups.google.com/d/msg/theano-dev/UbcYyU5m-M8/UO9UgXqnQP0J
    theano_rng = MRG_RandomStreams(10)
    w = theano.shared(numpy.ones(1, dtype='float32'))

    def one_step(x):
        return x + theano_rng.uniform((1,), dtype='float32') * w

    x = tensor.vector(dtype='float32')
    values, updates = theano.scan(one_step, outputs_info=x, n_steps=10)
    gw = theano.grad(tensor.sum(values[-1]), w)
    f = theano.function([x], gw)
    f(numpy.arange(1, dtype='float32'))


def test_multMatVect():
    A = tensor.imatrix('A')
    s = tensor.ivector('s')
    m = tensor.iscalar('m')
    
    g0 = rng_mrg.DotModulo()(A, s, m)
    f0 = theano.function([A, s, m], g0)
    
    A = numpy.random.randint(0, numpy.iinfo(numpy.int32).max, (3, 3)).astype('int32')
    s = numpy.random.randint(0, numpy.iinfo(numpy.int32).max, 3).astype('int32')
    m = numpy.asarray(numpy.random.randint(numpy.iinfo(numpy.int32).max), dtype="int32")
    
    f0.input_storage[0].storage[0] = A
    f0.input_storage[1].storage[0] = s
    f0.input_storage[2].storage[0] = m
    
    r_a = rng_mrg.matVecModM(A, s, m)
    r_b = f0.fn()
    
    assert numpy.allclose(r_a, r_b)
