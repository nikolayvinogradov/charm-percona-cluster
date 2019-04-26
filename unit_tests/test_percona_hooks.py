import json
import logging
import mock
import os
import shutil
import sys
import tempfile
import yaml

import charmhelpers.contrib.openstack.ha.utils as ch_ha_utils
from charmhelpers.contrib.database.mysql import PerconaClusterHelper

from test_utils import CharmTestCase

sys.modules['MySQLdb'] = mock.Mock()
# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
sys.modules['apt'] = mock.Mock()

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import percona_hooks as hooks


TO_PATCH = ['log', 'config',
            'get_db_helper',
            'relation_ids',
            'relation_set',
            'update_nrpe_config',
            'is_bootstrapped',
            'network_get_primary_address',
            'resolve_network_cidr',
            'unit_get',
            'resolve_hostname_to_ip',
            'is_clustered',
            'get_ipv6_addr',
            'update_hacluster_dns_ha',
            'update_hacluster_vip',
            'sst_password',
            'seeded',
            'is_leader',
            'leader_node_is_ready',
            'get_db_helper',
            'peer_store_and_set',
            'leader_get',
            'relation_clear',
            'is_relation_made',
            'is_sufficient_peers',
            'peer_retrieve_by_prefix',
            'client_node_is_ready',
            'relation_set',
            'relation_get']


class TestSharedDBRelation(CharmTestCase):

    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.network_get_primary_address.side_effect = NotImplementedError
        self.sst_password.return_value = 'ubuntu'

    def test_allowed_units_non_leader(self):
        self.seeded.return_value = True
        self.is_leader.return_value = False
        self.client_node_is_ready.return_value = True
        self.is_relation_made.return_value = True
        self.relation_ids.return_value = ['shared-db:3']
        self.peer_retrieve_by_prefix.return_value = {
            'password': 'pass123',
            'allowed_units': 'keystone/1 keystone/2'}
        hooks.shared_db_changed()
        self.relation_set.assert_called_once_with(
            allowed_units='keystone/1 keystone/2',
            password='pass123',
            relation_id='shared-db:3')

    @mock.patch.object(hooks, 'get_db_host')
    @mock.patch.object(hooks, 'configure_db_for_hosts')
    def test_allowed_units_leader(self, configure_db_for_hosts, get_db_host):
        self.config.return_value = None
        allowed_unit_mock = mock.MagicMock()
        allowed_unit_mock.get_allowed_units.return_value = [
            'keystone/1',
            'keystone/2']
        self.get_db_helper.return_value = allowed_unit_mock
        self.test_config.set('access-network', None)
        self.seeded.return_value = True
        self.is_leader.return_value = True
        self.resolve_hostname_to_ip.return_value = '10.0.0.10'
        self.relation_get.return_value = {
            'hostname': 'keystone-0',
            'database': 'keystone',
            'username': 'keyuser',
        }
        get_db_host.return_value = 'dbhost1'
        configure_db_for_hosts.return_value = 'password'
        hooks.shared_db_changed()
        self.relation_set.assert_called_once_with(
            allowed_units='keystone/1 keystone/2',
            relation_id=None)
        calls = [
            mock.call(
                relation_id=None,
                relation_settings={'access-network': None}),
            mock.call(
                relation_id=None,
                db_host='dbhost1',
                password='password',
                allowed_units='keystone/1 keystone/2')
        ]
        self.peer_store_and_set.assert_has_calls(calls)


class TestHARelation(CharmTestCase):
    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.network_get_primary_address.side_effect = NotImplementedError
        self.sst_password.return_value = 'ubuntu'

    def test_ha_relation_joined(self):
        # dns-ha: False
        self.config.return_value = False
        self.relation_ids.return_value = ['rid:23']

        def _add_vip_info(svc, rel_info):
            rel_info['groups'] = {
                'grp_mysql_vips': 'res_mysql_1e39e82_vip'}
            print(rel_info)
        self.update_hacluster_vip.side_effect = _add_vip_info
        hooks.ha_relation_joined()
        base_settings = {
            'clones': {
                'cl_mysql_monitor': (
                    'res_mysql_monitor meta interleave=true')},
            'colocations': {
                'colo_mysql': (
                    'inf: grp_mysql_vips '
                    'cl_mysql_monitor')},
            'resource_params': {
                'res_mysql_monitor': (
                    'params user="sstuser" '
                    'password="ubuntu" '
                    'pid="/var/run/mysqld/mysqld.pid" '
                    'socket="/var/run/mysqld/mysqld.sock" '
                    'max_slave_lag="5" '
                    'cluster_type="pxc" '
                    'op monitor interval="1s" '
                    'timeout="30s" '
                    'OCF_CHECK_LEVEL="1"')},
            'locations': {
                'loc_mysql': (
                    'grp_mysql_vips '
                    'rule inf: writable eq 1')},
            'resources': {
                'res_mysql_monitor': 'ocf:percona:mysql_monitor'},
            'delete_resources': ['loc_percona_cluster', 'grp_percona_cluster',
                                 'res_mysql_vip'],
            'groups': {
                'grp_mysql_vips': 'res_mysql_1e39e82_vip'}}
        self.update_hacluster_vip.assert_called_once_with(
            'mysql',
            base_settings)
        settings = {
            'json_{}'.format(k): json.dumps(v,
                                            **ch_ha_utils.JSON_ENCODE_OPTIONS)
            for k, v in base_settings.items() if v
        }
        self.relation_set.assert_called_once_with(
            relation_id='rid:23',
            **settings)

    def test_ha_relation_joined_dnsha(self):
        # dns-ha: False
        self.config.return_value = True
        self.relation_ids.return_value = ['rid:23']
        hooks.ha_relation_joined()
        base_settings = {
            'clones': {
                'cl_mysql_monitor': (
                    'res_mysql_monitor meta interleave=true')},
            'colocations': {
                'colo_mysql': (
                    'inf: grp_mysql_hostnames '
                    'cl_mysql_monitor')},
            'resource_params': {
                'res_mysql_monitor': (
                    'params user="sstuser" '
                    'password="ubuntu" '
                    'pid="/var/run/mysqld/mysqld.pid" '
                    'socket="/var/run/mysqld/mysqld.sock" '
                    'max_slave_lag="5" '
                    'cluster_type="pxc" '
                    'op monitor interval="1s" '
                    'timeout="30s" '
                    'OCF_CHECK_LEVEL="1"')},
            'locations': {
                'loc_mysql': (
                    'grp_mysql_hostnames '
                    'rule inf: writable eq 1')},
            'delete_resources': ['loc_percona_cluster', 'grp_percona_cluster',
                                 'res_mysql_vip'],
            'resources': {
                'res_mysql_monitor': 'ocf:percona:mysql_monitor'}}
        self.update_hacluster_dns_ha.assert_called_once_with(
            'mysql',
            base_settings)
        settings = {
            'json_{}'.format(k): json.dumps(v,
                                            **ch_ha_utils.JSON_ENCODE_OPTIONS)
            for k, v in base_settings.items() if v
        }
        self.relation_set.assert_called_once_with(
            relation_id='rid:23',
            **settings)


class TestHostResolution(CharmTestCase):
    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.network_get_primary_address.side_effect = NotImplementedError
        self.is_clustered.return_value = False
        self.config.side_effect = self.test_config.get
        self.test_config.set('prefer-ipv6', False)

    def test_get_db_host_defaults(self):
        '''
        Ensure that with nothing other than defaults private-address is used
        '''
        self.unit_get.return_value = 'mydbhost'
        self.resolve_hostname_to_ip.return_value = '10.0.0.2'
        self.assertEqual(hooks.get_db_host('myclient'), 'mydbhost')

    def test_get_db_host_network_spaces(self):
        '''
        Ensure that if the shared-db relation is bound, its bound address
        is used
        '''
        self.resolve_hostname_to_ip.return_value = '10.0.0.2'
        self.network_get_primary_address.side_effect = None
        self.network_get_primary_address.return_value = '192.168.20.2'
        self.assertEqual(hooks.get_db_host('myclient'), '192.168.20.2')
        self.network_get_primary_address.assert_called_with('shared-db')

    def test_get_db_host_network_spaces_clustered(self):
        '''
        Ensure that if the shared-db relation is bound and the unit is
        clustered, that the correct VIP is chosen
        '''
        self.resolve_hostname_to_ip.return_value = '10.0.0.2'
        self.is_clustered.return_value = True
        self.test_config.set('vip', '10.0.0.100 192.168.20.200')
        self.network_get_primary_address.side_effect = None
        self.network_get_primary_address.return_value = '192.168.20.2'
        self.resolve_network_cidr.return_value = '192.168.20.2/24'
        self.assertEqual(hooks.get_db_host('myclient'), '192.168.20.200')
        self.network_get_primary_address.assert_called_with('shared-db')


class TestNRPERelation(CharmTestCase):
    def setUp(self):
        patch_targets_nrpe = TO_PATCH[:]
        patch_targets_nrpe.remove("update_nrpe_config")
        patch_targets_nrpe.append("nrpe")
        patch_targets_nrpe.append("apt_install")
        CharmTestCase.setUp(self, hooks, patch_targets_nrpe)

    def test_mysql_monitored(self):
        """The mysql service is monitored by Nagios."""
        hooks.update_nrpe_config()
        self.nrpe.add_init_service_checks.assert_called_once_with(
            mock.ANY, ["mysql"], mock.ANY)


class TestMasterRelation(CharmTestCase):
    def setUp(self):
        patch_targets_master = TO_PATCH[:]
        patch_targets_master.extend(['configure_master',
                                     'get_cluster_id',
                                     'get_master_status',
                                     'leader_set'])
        CharmTestCase.setUp(self, hooks, patch_targets_master)

    def test_master_joined_is_leader_and_no_leader_change(self):
        self.relation_ids.return_value = ['master:1']
        self.get_cluster_id.return_value = 1
        self.is_clustered.return_value = True
        self.leader_get.return_value = {'async-rep-password': 'password',
                                        'master-address': '10.0.0.1',
                                        'master-file': 'file1',
                                        'master-position': 'position1'}
        self.is_leader.return_value = True
        self.configure_master.return_value = True
        self.get_master_status.return_value = '10.0.0.1', 'file1', 'position1'

        hooks.master_joined()
        self.leader_set.assert_called_with(
            {'async-rep-password': 'password',
             'master-address': '10.0.0.1',
             'master-file': 'file1',
             'master-position': 'position1'})
        self.relation_set.assert_called_with(
            relation_id='master:1', relation_settings={
                'leader': True, 'cluster_id': 1, 'master_address': '10.0.0.1',
                'master_file': 'file1', 'master_password': 'password',
                'master_position': 'position1'})

    def test_master_joined_is_leader_and_leader_change(self):
        self.relation_ids.return_value = ['master:1']
        self.get_cluster_id.return_value = 1
        self.is_clustered.return_value = True
        self.leader_get.return_value = {'async-rep-password': 'password',
                                        'master-address': '10.0.0.1',
                                        'master-file': 'file1',
                                        'master-position': 'position1'}
        self.is_leader.return_value = True
        self.configure_master.return_value = True
        self.get_master_status.return_value = '10.0.0.2', 'file2', 'position2'

        hooks.master_joined()
        self.leader_set.assert_called_with(
            {'async-rep-password': 'password',
             'master-address': '10.0.0.2',
             'master-file': 'file2',
             'master-position': 'position2'})
        self.relation_set.assert_called_with(
            relation_id='master:1', relation_settings={
                'leader': True, 'cluster_id': 1, 'master_address': '10.0.0.2',
                'master_file': 'file2', 'master_password': 'password',
                'master_position': 'position2'})

    def test_master_joined_is_not_leader(self):
        self.relation_ids.return_value = ['master:1']
        self.get_cluster_id.return_value = 1
        self.is_clustered.return_value = True
        self.leader_get.return_value = {'async-rep-password': 'password',
                                        'master-address': '10.0.0.1',
                                        'master-file': 'file',
                                        'master-position': 'position'}
        self.is_leader.return_value = False

        hooks.master_joined()
        self.relation_set.assert_called_with(
            relation_id='master:1', relation_settings={
                'leader': False, 'cluster_id': 1, 'master_address': '10.0.0.1',
                'master_file': 'file', 'master_password': 'password',
                'master_position': 'position'})


class TestSlaveRelation(CharmTestCase):
    def setUp(self):
        patch_targets_slave = TO_PATCH[:]
        patch_targets_slave.extend(["configure_slave",
                                    "get_cluster_id"])
        CharmTestCase.setUp(self, hooks, patch_targets_slave)

    def test_slave_joined(self):
        self.relation_ids.return_value = ['slave:1']
        self.is_clustered.return_value = True
        self.get_cluster_id.return_value = 1
        self.is_leader.return_value = True
        self.configure_slave.return_value = True
        self.network_get_primary_address.return_value = '172.16.0.1'

        hooks.slave_joined()
        self.relation_set.assert_called_with(
            relation_id='slave:1', relation_settings={
                'slave_address': '172.16.0.1', 'cluster_id': 1})


class TestConfigChanged(CharmTestCase):

    TO_PATCH = [
        'log',
        'open_port',
        'config',
        'is_unit_paused_set',
        'get_cluster_hosts',
        'is_leader_bootstrapped',
        'is_bootstrapped',
        'clustered_once',
        'is_leader',
        'is_sufficient_peers',
        'render_config_restart_on_changed',
        'update_client_db_relations',
        'install_mysql_ocf',
        'relation_ids',
        'is_relation_made',
        'ha_relation_joined',
        'update_nrpe_config',
        'assert_charm_supports_ipv6',
        'update_bootstrap_uuid',
        'update_root_password',
        'install_percona_xtradb_cluster',
        'get_cluster_hosts',
        'leader_get',
        'set_ready_on_peers',
        'is_unit_paused_set',
        'is_unit_upgrading_set',
    ]

    def setUp(self):
        CharmTestCase.setUp(self, hooks, self.TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.is_unit_paused_set.return_value = False
        self.is_unit_upgrading_set.return_value = False
        self.is_leader.return_value = False
        self.is_leader_bootstrapped.return_value = False
        self.is_bootstrapped.return_value = False
        self.clustered_once.return_value = False
        self.relation_ids.return_value = []
        self.is_relation_made.return_value = False
        self.get_cluster_hosts.return_value = []

        def _leader_get(key):
            settings = {'leader-ip': '10.10.10.10',
                        'cluster_series_upgrading': False}
            return settings.get(key)
        self.leader_get.side_effect = _leader_get

    def test_config_changed_open_port(self):
        '''Ensure open_port is called with MySQL default port'''
        self.is_leader_bootstrapped.return_value = True
        hooks.config_changed()
        self.open_port.assert_called_with(3306)

    def test_config_changed_render_leader(self):
        '''Ensure configuration is only rendered when ready for the leader'''
        self.is_leader.return_value = True

        # Render without peers, leader not bootsrapped
        self.get_cluster_hosts.return_value = []
        hooks.config_changed()
        self.install_percona_xtradb_cluster.assert_called_once()
        self.render_config_restart_on_changed.assert_called_once_with(
            [], bootstrap=True)

        # Render without peers, leader bootstrapped
        self.is_leader_bootstrapped.return_value = True
        self.get_cluster_hosts.return_value = []
        self.render_config_restart_on_changed.reset_mock()
        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            [], bootstrap=False)

        # Render without hosts, leader bootstrapped, never clustered
        self.is_leader_bootstrapped.return_value = True
        self.get_cluster_hosts.return_value = ['10.10.10.20', '10.10.10.30']

        self.render_config_restart_on_changed.reset_mock()
        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            [], bootstrap=False)

        # Clustered at least once
        self.clustered_once.return_value = True

        # Render with hosts, leader bootstrapped
        self.is_leader_bootstrapped.return_value = True
        self.get_cluster_hosts.return_value = ['10.10.10.20', '10.10.10.30']

        self.render_config_restart_on_changed.reset_mock()
        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            ['10.10.10.20', '10.10.10.30'], bootstrap=False)

        # In none of the prior scenarios should update_root_password have been
        # called.
        self.update_root_password.assert_not_called()

        # Render with hosts, leader and cluster bootstrapped
        self.is_leader_bootstrapped.return_value = True
        self.is_bootstrapped.return_value = True
        self.get_cluster_hosts.return_value = ['10.10.10.20', '10.10.10.30']

        self.render_config_restart_on_changed.reset_mock()
        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            ['10.10.10.20', '10.10.10.30'], bootstrap=False)
        self.update_root_password.assert_called_once()

    def test_config_changed_render_non_leader(self):
        '''Ensure configuration is only rendered when ready for
        non-leaders'''

        # Avoid rendering for non-leader.
        # Bug #1738896
        # Leader not bootstrapped
        # Do not render
        self.get_cluster_hosts.return_value = ['10.10.10.20', '10.10.10.30',
                                               '10.10.10.10']
        self.is_leader_bootstrapped.return_value = False
        hooks.config_changed()
        self.install_percona_xtradb_cluster.assert_called_once_with()
        self.render_config_restart_on_changed.assert_not_called()
        self.update_bootstrap_uuid.assert_not_called()

        # Leader is bootstrapped, insufficient peers
        # Do not render
        self.is_sufficient_peers.return_value = False
        self.is_leader_bootstrapped.return_value = True
        self.render_config_restart_on_changed.reset_mock()
        self.install_percona_xtradb_cluster.reset_mock()

        hooks.config_changed()
        self.install_percona_xtradb_cluster.assert_called_once_with()
        self.render_config_restart_on_changed.assert_not_called()
        self.update_bootstrap_uuid.assert_not_called()

        # Leader is bootstrapped, sufficient peers
        # Use the leader node and render.
        self.is_sufficient_peers.return_value = True
        self.is_leader_bootstrapped.return_value = True
        self.get_cluster_hosts.return_value = []
        self.render_config_restart_on_changed.reset_mock()
        self.install_percona_xtradb_cluster.reset_mock()

        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            ['10.10.10.10'])

        # Missing leader, leader bootstrapped
        # Bug #1738896
        # Leader bootstrapped
        # Add the leader node and render.
        self.render_config_restart_on_changed.reset_mock()
        self.update_bootstrap_uuid.reset_mock()
        self.get_cluster_hosts.return_value = ['10.10.10.20', '10.10.10.30']

        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            ['10.10.10.10', '10.10.10.20', '10.10.10.30'])
        self.update_bootstrap_uuid.assert_called_once()

        # Leader present, leader bootstrapped
        self.render_config_restart_on_changed.reset_mock()
        self.update_bootstrap_uuid.reset_mock()
        self.get_cluster_hosts.return_value = ['10.10.10.20', '10.10.10.30',
                                               '10.10.10.10']

        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            ['10.10.10.20', '10.10.10.30', '10.10.10.10'])
        self.update_bootstrap_uuid.assert_called_once()

        # In none of the prior scenarios should update_root_password have been
        # called. is_bootstrapped was defaulted to False
        self.update_root_password.assert_not_called()
        self.set_ready_on_peers.assert_not_called()

        # Leader present, leader bootstrapped, cluster bootstrapped
        self.is_bootstrapped.return_value = True
        self.render_config_restart_on_changed.reset_mock()
        self.update_bootstrap_uuid.reset_mock()
        self.get_cluster_hosts.return_value = ['10.10.10.20', '10.10.10.30',
                                               '10.10.10.10']

        hooks.config_changed()
        self.render_config_restart_on_changed.assert_called_once_with(
            ['10.10.10.20', '10.10.10.30', '10.10.10.10'])
        self.update_bootstrap_uuid.assert_called_once()
        self.update_root_password.assert_called_once()
        self.set_ready_on_peers.called_once()


class TestInstallPerconaXtraDB(CharmTestCase):

    TO_PATCH = [
        'log',
        'pxc_installed',
        'root_password',
        'sst_password',
        'configure_mysql_root_password',
        'apt_install',
        'determine_packages',
        'configure_sstuser',
        'config',
        'run_mysql_checks',
        'is_leader_bootstrapped',
        'is_leader',
    ]

    def setUp(self):
        CharmTestCase.setUp(self, hooks, self.TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.pxc_installed.return_value = False

    def test_installed(self):
        self.pxc_installed.return_value = True
        hooks.install_percona_xtradb_cluster()
        self.configure_mysql_root_password.assert_not_called()
        self.apt_install.assert_not_called()

    def test_passwords_not_initialized(self):
        self.root_password.return_value = None
        self.sst_password.return_value = None
        hooks.install_percona_xtradb_cluster()
        self.configure_mysql_root_password.assert_not_called()
        self.configure_sstuser.assert_not_called()
        self.apt_install.assert_not_called()
        self.is_leader_bootstrapped.return_value = True

        self.root_password.return_value = None
        self.sst_password.return_value = 'testpassword'
        hooks.install_percona_xtradb_cluster()
        self.configure_sstuser.assert_not_called()
        self.configure_mysql_root_password.assert_not_called()
        self.apt_install.assert_not_called()

    def test_passwords_initialized(self):
        self.root_password.return_value = 'rootpassword'
        self.sst_password.return_value = 'testpassword'
        self.determine_packages.return_value = ['pxc-5.6']
        self.is_leader_bootstrapped.return_value = True
        hooks.install_percona_xtradb_cluster()
        self.configure_mysql_root_password.assert_called_with('rootpassword')
        self.configure_sstuser.assert_called_with('testpassword')
        self.apt_install.assert_called_with(['pxc-5.6'], fatal=True)
        self.run_mysql_checks.assert_not_called()


class TestUpgradeCharm(CharmTestCase):
    TO_PATCH = [
        'config',
        'log',
        'is_leader',
        'is_unit_paused_set',
        'get_wsrep_value',
        'config_changed',
        'get_relation_ip',
        'leader_set',
        'sst_password',
        'configure_sstuser',
        'leader_get',
        'notify_bootstrapped',
        'mark_seeded',
    ]

    def print_log(self, msg, level=None):
        print('juju-log: %s: %s' % (level, msg))

    def setUp(self):
        CharmTestCase.setUp(self, hooks, self.TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.log.side_effect = self.print_log
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        CharmTestCase.tearDown(self)
        try:
            shutil.rmtree(self.tmpdir)
        except:
            pass

    def test_upgrade_charm_leader(self):
        self.is_leader.return_value = True
        self.is_unit_paused_set.return_value = False
        self.get_relation_ip.return_value = '10.10.10.10'
        self.leader_get.side_effect = [None, 'mypasswd', 'mypasswd']

        def c(k):
            values = {'wsrep_ready': 'on',
                      'wsrep_cluster_state_uuid': '1234-abcd'}
            return values[k]

        self.get_wsrep_value.side_effect = c

        hooks.upgrade()

        self.mark_seeded.assert_called_once()
        self.notify_bootstrapped.assert_called_with(cluster_uuid='1234-abcd')
        self.configure_sstuser.assert_called_once()

        self.leader_set.assert_has_calls(
            [mock.call(**{'leader-ip': '10.10.10.10'}),
             mock.call(**{'root-password': 'mypasswd'})])


class TestConfigs(CharmTestCase):

    TO_PATCH = [
        'config',
        'is_leader',
    ]

    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.default_config = self._get_default_config()
        for key, value in self.default_config.items():
            self.test_config.set(key, value)
        self.is_leader.return_value = False

    def _load_config(self):
        '''Walk backwords from __file__ looking for config.yaml,
        load and return the 'options' section'
        '''
        config = None
        f = __file__
        while config is None:
            d = os.path.dirname(f)
            if os.path.isfile(os.path.join(d, 'config.yaml')):
                config = os.path.join(d, 'config.yaml')
                break
            f = d

        if not config:
            logging.error('Could not find config.yaml in any parent directory '
                          'of %s. ' % f)
            raise Exception

        return yaml.safe_load(open(config).read())['options']

    def _get_default_config(self):
        '''Load default charm config from config.yaml return as a dict.
        If no default is set in config.yaml, its value is None.
        '''
        default_config = {}
        config = self._load_config()
        for k, v in config.iteritems():
            if 'default' in v:
                default_config[k] = v['default']
            else:
                default_config[k] = None
        return default_config

    @mock.patch.object(os, 'makedirs')
    @mock.patch.object(hooks, 'get_cluster_host_ip')
    @mock.patch.object(hooks, 'get_wsrep_provider_options')
    @mock.patch.object(PerconaClusterHelper, 'parse_config')
    @mock.patch.object(hooks, 'render')
    @mock.patch.object(hooks, 'sst_password')
    @mock.patch.object(hooks, 'lsb_release')
    def test_render_config_defaults_xenial(self,
                                           lsb_release,
                                           sst_password,
                                           render,
                                           parse_config,
                                           get_wsrep_provider_options,
                                           get_cluster_host_ip,
                                           makedirs):
        parse_config.return_value = {'key_buffer': '32M'}
        get_cluster_host_ip.return_value = '10.1.1.1'
        get_wsrep_provider_options.return_value = None
        sst_password.return_value = 'sstpassword'
        lsb_release.return_value = {'DISTRIB_CODENAME': 'xenial'}
        context = {
            'wsrep_slave_threads': 1,
            'server-id': hooks.get_server_id(),
            'is_leader': hooks.is_leader(),
            'series_upgrade': hooks.is_unit_upgrading_set(),
            'private_address': '10.1.1.1',
            'cluster_hosts': '',
            'enable_binlogs': self.default_config['enable-binlogs'],
            'sst_password': 'sstpassword',
            'myisam_recover': 'BACKUP',
            'sst_method': self.default_config['sst-method'],
            'server_id': hooks.get_server_id(),
            'binlogs_max_size': self.default_config['binlogs-max-size'],
            'key_buffer': '32M',
            'performance_schema': self.default_config['performance-schema'],
            'binlogs_path': self.default_config['binlogs-path'],
            'cluster_name': 'juju_cluster',
            'binlogs_expire_days': self.default_config['binlogs-expire-days'],
            'ipv6': False,
            'innodb_file_per_table':
            self.default_config['innodb-file-per-table'],
            'table_open_cache': self.default_config['table-open-cache'],
            'wsrep_provider': '/usr/lib/libgalera_smm.so',
        }

        hooks.render_config()
        hooks.render.assert_called_once_with(
            'mysqld.cnf',
            '/etc/mysql/percona-xtradb-cluster.conf.d/mysqld.cnf',
            context,
            perms=0o444)

    @mock.patch.object(os, 'makedirs')
    @mock.patch.object(hooks, 'get_cluster_host_ip')
    @mock.patch.object(hooks, 'get_wsrep_provider_options')
    @mock.patch.object(PerconaClusterHelper, 'parse_config')
    @mock.patch.object(hooks, 'render')
    @mock.patch.object(hooks, 'sst_password')
    @mock.patch.object(hooks, 'lsb_release')
    def test_render_config_defaults(self,
                                    lsb_release,
                                    sst_password,
                                    render,
                                    parse_config,
                                    get_wsrep_provider_options,
                                    get_cluster_host_ip,
                                    makedirs):
        parse_config.return_value = {'key_buffer': '32M'}
        get_cluster_host_ip.return_value = '10.1.1.1'
        get_wsrep_provider_options.return_value = None
        sst_password.return_value = 'sstpassword'
        lsb_release.return_value = {'DISTRIB_CODENAME': 'bionic'}
        context = {
            'wsrep_slave_threads': 48,
            'server_id': hooks.get_server_id(),
            'server-id': hooks.get_server_id(),
            'is_leader': hooks.is_leader(),
            'series_upgrade': hooks.is_unit_upgrading_set(),
            'private_address': '10.1.1.1',
            'innodb_autoinc_lock_mode': '2',
            'cluster_hosts': '',
            'enable_binlogs': self.default_config['enable-binlogs'],
            'sst_password': 'sstpassword',
            'sst_method': self.default_config['sst-method'],
            'pxc_strict_mode': 'enforcing',
            'binlogs_max_size': self.default_config['binlogs-max-size'],
            'cluster_name': 'juju_cluster',
            'innodb_file_per_table':
            self.default_config['innodb-file-per-table'],
            'table_open_cache': self.default_config['table-open-cache'],
            'binlogs_path': self.default_config['binlogs-path'],
            'binlogs_expire_days': self.default_config['binlogs-expire-days'],
            'performance_schema': self.default_config['performance-schema'],
            'key_buffer': '32M',
            'default_storage_engine': 'InnoDB',
            'wsrep_log_conflicts': True,
            'ipv6': False,
            'wsrep_provider': '/usr/lib/galera3/libgalera_smm.so',
        }

        hooks.render_config()
        hooks.render.assert_called_once_with(
            'mysqld.cnf',
            '/etc/mysql/percona-xtradb-cluster.conf.d/mysqld.cnf',
            context,
            perms=0o444)

    @mock.patch.object(os, 'makedirs')
    @mock.patch.object(hooks, 'get_cluster_host_ip')
    @mock.patch.object(hooks, 'get_wsrep_provider_options')
    @mock.patch.object(PerconaClusterHelper, 'parse_config')
    @mock.patch.object(hooks, 'render')
    @mock.patch.object(hooks, 'sst_password')
    @mock.patch.object(hooks, 'lsb_release')
    def test_render_config_wsrep_slave_threads(
            self,
            lsb_release,
            sst_password,
            render,
            parse_config,
            get_wsrep_provider_options,
            get_cluster_host_ip,
            makedirs):
        parse_config.return_value = {'key_buffer': '32M'}
        get_cluster_host_ip.return_value = '10.1.1.1'
        get_wsrep_provider_options.return_value = None
        sst_password.return_value = 'sstpassword'
        self.test_config.set('wsrep-slave-threads', 2)
        lsb_release.return_value = {'DISTRIB_CODENAME': 'bionic'}

        context = {
            'server_id': hooks.get_server_id(),
            'server-id': hooks.get_server_id(),
            'is_leader': hooks.is_leader(),
            'series_upgrade': hooks.is_unit_upgrading_set(),
            'private_address': '10.1.1.1',
            'innodb_autoinc_lock_mode': '2',
            'cluster_hosts': '',
            'enable_binlogs': self.default_config['enable-binlogs'],
            'sst_password': 'sstpassword',
            'sst_method': self.default_config['sst-method'],
            'pxc_strict_mode': 'enforcing',
            'binlogs_max_size': self.default_config['binlogs-max-size'],
            'cluster_name': 'juju_cluster',
            'innodb_file_per_table':
            self.default_config['innodb-file-per-table'],
            'table_open_cache': self.default_config['table-open-cache'],
            'binlogs_path': self.default_config['binlogs-path'],
            'binlogs_expire_days': self.default_config['binlogs-expire-days'],
            'performance_schema': self.default_config['performance-schema'],
            'key_buffer': '32M',
            'default_storage_engine': 'InnoDB',
            'wsrep_log_conflicts': True,
            'ipv6': False,
            'wsrep_provider': '/usr/lib/galera3/libgalera_smm.so',
            'wsrep_slave_threads': 2,
        }

        hooks.render_config()
        hooks.render.assert_called_once_with(
            'mysqld.cnf',
            '/etc/mysql/percona-xtradb-cluster.conf.d/mysqld.cnf',
            context,
            perms=0o444)
