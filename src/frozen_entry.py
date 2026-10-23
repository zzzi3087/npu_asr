"""Windows GUI entry point; diagnostics run only with explicit developer flags."""
import os,sys,multiprocessing
os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OMP_WAIT_POLICY','PASSIVE')

if __name__=='__main__':
    multiprocessing.freeze_support()
    if '--smoke-test' in sys.argv:
        from package_smoke import main
    else:
        from app import main
    sys.exit(main())
