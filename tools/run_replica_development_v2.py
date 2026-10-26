"""Use the frozen first pilot implementation in a distinct retry output path."""
import run_replica_development as pilot


if __name__=='__main__':
    pilot.OUT=pilot.ex.ROOT/'results/replica-development-v2'
    pilot.STATE=pilot.OUT/'capacity-fidelity.json'
    pilot.main()
