import logging

from virttest import virsh
from virttest.utils_zcrypt import CryptoDeviceInfoBuilder, \
    APMaskHelper, MatrixDevice, load_vfio_ap, unload_vfio_ap
from tempfile import mktemp
from virttest.utils_misc import wait_for
from utils_misc import cmd_status_output

# minimal supported hwtype
MIN_HWTYPE = 11


def find_devices_by_cap(test, cap_type):
    """
    Find device by capability
    :params cap_type: capability type
    """
    result = virsh.nodedev_list(cap=cap_type)
    if result.exit_status:
        test.fail(result.stderr)

    device_name = result.stdout.strip().splitlines()
    return device_name


def create_nodedev_from_xml(test, params):
    """
    Create a device defined by an XML file on the node
    :params: the parameter dictionary
    """
    dev_name = params.get("nodedev_dev_name")
    status, uuid = cmd_status_output('uuidgen')
    if dev_name == "crypto":
        device_xml = """
<device>
<parent>ap_matrix</parent>
<capability type='mdev'>
<uuid>%s</uuid>
<type id='vfio_ap-passthrough'/>
</capability>
</device>
    """ % uuid
        logging.debug("Prepare the nodedev XML: %s", device_xml)
        uri = params.get("virsh_uri")
        device_file = mktemp()
        result = virsh.nodedev_create(device_file, uri=uri, debug=True)
        status = result.exit_status
        if status:
            test.fail(result.stderr)
        else:
            output = result.stdout.strip()
            logging.info(output)
    return uuid


def destroy_nodedev(test, params):
    """
    Destroy (stop) a device on the node
    :params: the parameter dictionary
    """
    dev_name = params.get("nodedev_dev_name")
    if dev_name == "crypto":
        dev_name = params.get("crypto")
    uri = params.get("virsh_uri")
    result = virsh.nodedev_destroy(dev_name, uri=uri, debug=True)
    # Check nodedev value
    mdev_cap = params.get('mdev_cap')
    if not find_devices_by_cap(mdev_cap):
        logging.info(result.stdout.strip())
    else:
        test.fail("The relevant directory still exists"
                  "or mismatch with result")


def run(test, params, env):
    '''
    1. Check if the crypto device exist in host
    2. Passthrough the crypto device
    2. Create the mdev
    3. Confirm the mdev was created successfully
    4. Confirm device availability in guest
    5. Destroy the mdev
    6. Confirm the mdev was destroyed successfully

    :param test: test object
    :param params: Dict with test test parameters
    :param env: Dict with the test environment
    :return:
    '''

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    matrix_cap = params.get('matrix_cap')
    mdev_cap = params.get('mdev_cap')

    try:
        info = CryptoDeviceInfoBuilder.get()
        if not info.entries or int(info.domains[0].hwtype) < MIN_HWTYPE:
            test.error("vfio-ap requires at least HWTYPE %s." % MIN_HWTYPE)
        if not find_devices_by_cap(test, matrix_cap):
            load_vfio_ap()
            if find_devices_by_cap(test, matrix_cap):
                devices = [info.domains[0]]
                APMaskHelper.from_infos(devices)
                uuid = create_nodedev_from_xml(test, params)
                mdev_cap = mdev_cap + '_%s' % uuid.replace('-', '_')
            if find_devices_by_cap(test, mdev_cap):
                session = vm.wait_for_login()

            def verify_passed_through():
                guest_info = CryptoDeviceInfoBuilder.get(session)
                logging.debug("Guest lszcrypt got %s", guest_info)
                if guest_info.domains:
                    default_driver_on_host = devices[0].driver
                    driver_in_guest = guest_info.domains[0].driver
                    logging.debug("Expecting default drivers from host and"
                                  "guest to be the same: { host: %s, guest:"
                                  "%s }", default_driver_on_host,
                                  driver_in_guest)
                    return default_driver_on_host == driver_in_guest
                return False
            if not wait_for(verify_passed_through, timeout=60, step=10):
                test.fail("Crypto domain not attached correctly in guest."
                          " Please, check the test log for details.")
            else:
                destroy_nodedev(test, params)
                unload_vfio_ap()
    finally:
        if find_devices_by_cap(matrix_cap):
            raise OSError("%s did not removed correctly checking by nodedev-API"
                          ) % matrix_cap
