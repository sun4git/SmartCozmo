from cozmo_brain.config import Settings
from cozmo_brain.robot.base import RobotBackend


def create_robot(settings: Settings, force_simulated: bool = False) -> RobotBackend:
    backend = "simulated" if force_simulated else settings.robot_backend.lower()

    if backend == "simulated":
        from cozmo_brain.robot.simulated import SimulatedRobot

        return SimulatedRobot()

    if backend == "real":
        from cozmo_brain.robot.real import PyCozmoRobot

        return PyCozmoRobot(settings)

    raise ValueError(f"Unknown ROBOT_BACKEND '{backend}'. Use 'real' or 'simulated'.")


__all__ = ["RobotBackend", "create_robot"]
